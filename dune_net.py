"""
Dune: Awakening - Generalized Network Routing, NAT & Cluster Sync Engine (Version 2.5.0)
Deterministic topology, Hyper-V lifecycle handling, strict L4 Hairpin NAT, and automated K3s IP Synchronization.
"""

import argparse
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import time
import traceback
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

if sys.platform == "win32":
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except Exception:
        pass


class Color:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    GRAY = "\033[90m"


def print_info(msg: str):
    print(f"{Color.CYAN}[*] {msg}{Color.RESET}")


def print_success(msg: str):
    print(f"{Color.GREEN}[+] {msg}{Color.RESET}")


def print_warning(msg: str):
    print(f"{Color.YELLOW}[!] {msg}{Color.RESET}")


def print_error(msg: str):
    print(f"{Color.RED}[-] ERROR: {msg}{Color.RESET}")


@dataclass
class TopologyContext:
    public_ip: str
    vm_ip: str
    pc_ip: str
    vm_user: str
    switch_alias: str
    is_same_subnet: bool
    subnet_cidr: str
    game_udp_ports: str
    node_tcp_ports: str
    settings_file: str
    k8s_namespace: Optional[str] = None
    k8s_gateway_deploy: Optional[str] = None


class VmLifecycleManager:
    @staticmethod
    def get_vm_state() -> Tuple[Optional[str], Optional[str]]:
        ps_cmd = (
            "Get-VM -ErrorAction SilentlyContinue | "
            "Where-Object { $_.Name -like '*Dune*' -or $_.NetworkAdapters.SwitchName -like '*Dune*' } | "
            "Select-Object -First 1 Name, State | ConvertTo-Json"
        )
        data = NetworkDiscovery.run_powershell_json(ps_cmd)
        if data and isinstance(data, dict):
            return data.get("Name"), str(data.get("State", "")).strip()
        return None, None

    @classmethod
    def handle_offline_vm(cls, vm_name: Optional[str], state: Optional[str]) -> bool:
        name_str = vm_name or "Dune Awakening Dedicated Server"
        state_str = state or "Off"

        print_warning(f"Virtual Machine '{name_str}' is NOT running (State: {state_str}).")
        print(f"\n{Color.CYAN}Required Actions in Battlegroup CLI:{Color.RESET}")
        print("  1. Start the VM using option: 'b. start-vm'")
        print("  2. If Public IP changed, update it using: '8. change-battlegroup-ip'")
        print("  3. Start the game server using: '2. start'")
        print(f"  {Color.GRAY}* Note: If players cannot see server, verify updates using: '5. update'{Color.RESET}\n")

        battlegroup_bat = os.path.join(os.path.dirname(os.path.abspath(__file__)), "battlegroup.bat")
        try:
            choice = input(f"{Color.CYAN}[?] Launch battlegroup.bat now to manage VM? (Y/N): {Color.RESET}").strip()
            if choice.lower() == 'y':
                if os.path.exists(battlegroup_bat):
                    print_info("Launching battlegroup.bat...")
                    subprocess.Popen(["cmd.exe", "/c", battlegroup_bat], creationflags=subprocess.CREATE_NEW_CONSOLE)
                else:
                    print_error("'battlegroup.bat' was not found in the root directory.")
        except (KeyboardInterrupt, EOFError):
            pass

        return False


class NetworkDiscovery:
    RESOLVERS = [
        "https://api.ipify.org",
        "https://ifconfig.me/ip",
        "https://icanhazip.com"
    ]

    @classmethod
    def query_public_ip(cls) -> str:
        for url in cls.RESOLVERS:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "DuneNetEngine/2.5"})
                with urllib.request.urlopen(req, timeout=4) as response:
                    candidate = response.read().decode("utf-8").strip()
                    ipaddress.IPv4Address(candidate)
                    return candidate
            except Exception:
                continue
        raise RuntimeError("Failed to resolve public IPv4 address through all configured resolvers.")

    @staticmethod
    def run_powershell_json(script: str) -> Any:
        cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0 or not proc.stdout.strip():
            return None
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            return None

    @classmethod
    def resolve_host_interface(cls, preferred_switch: str) -> Tuple[Optional[str], Optional[str], Optional[int]]:
        query = (
            "Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | "
            "Where-Object { $_.InterfaceAlias -notlike '*Loopback*' -and $_.IPAddress -notlike '169.254.*' } | "
            "Select-Object InterfaceAlias, IPAddress, PrefixLength | ConvertTo-Json"
        )
        data = cls.run_powershell_json(query)
        if not data:
            return None, None, None

        items = data if isinstance(data, list) else [data]

        if preferred_switch:
            for item in items:
                if preferred_switch.lower() in item.get("InterfaceAlias", "").lower():
                    return item["InterfaceAlias"], item["IPAddress"], item["PrefixLength"]

        for item in items:
            alias = item.get("InterfaceAlias", "")
            if "dune" in alias.lower():
                return alias, item["IPAddress"], item["PrefixLength"]

        for item in items:
            alias = item.get("InterfaceAlias", "")
            if "vethernet" in alias.lower():
                return alias, item["IPAddress"], item["PrefixLength"]

        first = items[0]
        return first["InterfaceAlias"], first["IPAddress"], first["PrefixLength"]

    @staticmethod
    def check_ssh_port(ip: str, timeout: float = 0.5) -> bool:
        try:
            with socket.create_connection((ip, 22), timeout=timeout):
                return True
        except (socket.timeout, OSError):
            return False

    @classmethod
    def resolve_vm_ip(cls, switch_name: str, host_ip: str, host_net: ipaddress.IPv4Network) -> Optional[str]:
        ps_hyperv = (
            "Get-VMNetworkAdapter -All -ErrorAction SilentlyContinue | "
            "Where-Object { $_.SwitchName -like '*Dune*' -or $_.VMName -like '*Dune*' } | "
            "Select-Object -ExpandProperty IPAddresses | "
            "Where-Object { $_ -match '^\\d{1,3}(\\.\\d{1,3}){3}$' -and $_ -notlike '169.254.*' } | "
            "ConvertTo-Json"
        )
        data = cls.run_powershell_json(ps_hyperv)
        if isinstance(data, list) and len(data) > 0:
            return data[0]
        if isinstance(data, str) and data:
            return data

        ps_neighbors = (
            f"Get-NetNeighbor -InterfaceAlias '{switch_name}' -AddressFamily IPv4 -ErrorAction SilentlyContinue | "
            f"Where-Object {{ $_.State -ne 'Unreachable' }} | "
            "Select-Object -ExpandProperty IPAddress | ConvertTo-Json"
        )
        n_data = cls.run_powershell_json(ps_neighbors)
        candidates = []
        if isinstance(n_data, list):
            candidates = n_data
        elif isinstance(n_data, str) and n_data:
            candidates = [n_data]

        valid_ips: List[str] = []
        for ip_str in candidates:
            try:
                ip_obj = ipaddress.IPv4Address(ip_str)
                if (
                    str(ip_obj) != host_ip
                    and str(ip_obj) != "255.255.255.255"
                    and not ip_obj.is_multicast
                    and not ip_obj.is_loopback
                    and ip_obj != host_net.broadcast_address
                    and ip_obj != host_net.network_address
                ):
                    valid_ips.append(str(ip_obj))
            except ValueError:
                continue

        for ip_candidate in valid_ips:
            if cls.check_ssh_port(ip_candidate):
                return ip_candidate

        if valid_ips:
            return valid_ips[0]

        return None

    @classmethod
    def wait_for_vm_readiness(cls, switch_name: str, host_ip: str, host_net: ipaddress.IPv4Network, timeout_sec: int = 25) -> Optional[str]:
        print_info("Waiting for VM network initialization and SSH daemon...")
        start_time = time.time()
        while time.time() - start_time < timeout_sec:
            ip = cls.resolve_vm_ip(switch_name, host_ip, host_net)
            if ip and cls.check_ssh_port(ip):
                return ip
            time.sleep(2)
            sys.stdout.write(".")
            sys.stdout.flush()
        print("")
        return None

    @classmethod
    def build_context(cls, config_path: str) -> Optional[TopologyContext]:
        vm_name, vm_state = VmLifecycleManager.get_vm_state()
        if vm_state and vm_state.lower() not in ["running", "2"]:
            VmLifecycleManager.handle_offline_vm(vm_name, vm_state)
            return None

        config: Dict[str, Any] = {}
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8-sig") as f:
                    config = json.load(f)
            except Exception as e:
                print_warning(f"Failed to parse '{config_path}': {e}. Using runtime discovery.")

        vm_user = config.get("vm_user") or "dune"
        preferred_switch = config.get("switch_name") or ""
        configured_vm_ip = config.get("vm_ip") or ""
        configured_pc_ip = config.get("pc_ip") or ""
        settings_file = config.get("settings_file") or "/home/dune/.dune/settings.conf"

        switch_alias, pc_ip, prefix_len = cls.resolve_host_interface(preferred_switch)
        if configured_pc_ip:
            pc_ip = configured_pc_ip
        if not pc_ip or not prefix_len:
            raise RuntimeError("Failed to discover active host IP and network prefix length.")

        host_net = ipaddress.IPv4Network(f"{pc_ip}/{prefix_len}", strict=False)

        vm_ip = configured_vm_ip or cls.resolve_vm_ip(switch_alias, pc_ip, host_net)
        if not vm_ip or not cls.check_ssh_port(vm_ip):
            vm_ip = cls.wait_for_vm_readiness(switch_alias, pc_ip, host_net, timeout_sec=20)

        if not vm_ip:
            raise RuntimeError(f"VM network adapter on switch '{switch_alias}' is unreachable or SSH is offline.")

        public_ip = cls.query_public_ip()
        vm_obj = ipaddress.IPv4Address(vm_ip)
        is_same_subnet = vm_obj in host_net
        subnet_cidr = str(host_net)

        game_ports = config.get("game_udp_ports") or "7777:7810"
        node_ports = config.get("node_tcp_ports") or "10000:32767"

        return TopologyContext(
            public_ip=public_ip,
            vm_ip=vm_ip,
            pc_ip=pc_ip,
            vm_user=vm_user,
            switch_alias=switch_alias,
            is_same_subnet=is_same_subnet,
            subnet_cidr=subnet_cidr,
            game_udp_ports=game_ports,
            node_tcp_ports=node_ports,
            settings_file=settings_file
        )


class SshExecutor:
    def __init__(self, user: str, host: str):
        self.user = user
        self.host = host

    def run(self, command: str) -> Tuple[int, str]:
        cmd = [
            "ssh.exe",
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=accept-new",
            f"{self.user}@{self.host}",
            command
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        out = (proc.stdout + "\n" + proc.stderr).strip()
        return proc.returncode, out


class RouteManager:
    @staticmethod
    def sync_windows_route(ctx: TopologyContext) -> bool:
        print_info("Synchronizing Windows Routing Table...")
        ps_query = (
            f"Get-NetRoute -DestinationPrefix '{ctx.public_ip}/32' -ErrorAction SilentlyContinue | "
            "Select-Object DestinationPrefix, NextHop, RouteMetric | ConvertTo-Json"
        )
        routes = NetworkDiscovery.run_powershell_json(ps_query)

        matched = False
        if routes:
            route_list = routes if isinstance(routes, list) else [routes]
            for r in route_list:
                if r.get("NextHop") == ctx.vm_ip and r.get("RouteMetric") == 1:
                    matched = True
                else:
                    print_warning(f"Purging invalid route: {r.get('DestinationPrefix')} via {r.get('NextHop')}")
                    ps_del = f"Remove-NetRoute -DestinationPrefix '{r.get('DestinationPrefix')}' -NextHop '{r.get('NextHop')}' -Confirm:$false"
                    subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_del], capture_output=True)

        if not matched:
            print_success(f"Injecting host route: {ctx.public_ip}/32 -> {ctx.vm_ip} (Metric: 1)")
            ps_add = f"New-NetRoute -DestinationPrefix '{ctx.public_ip}/32' -InterfaceAlias '{ctx.switch_alias}' -NextHop '{ctx.vm_ip}' -RouteMetric 1"
            res = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_add], capture_output=True, text=True)
            if res.returncode != 0:
                print_error(f"Failed to add Windows route: {res.stderr.strip()}")
                return False
        else:
            print_success(f"Route already active and verified: {ctx.public_ip}/32 -> {ctx.vm_ip} (Metric: 1)")
        return True

    @staticmethod
    def clean_windows_route(ctx: TopologyContext):
        print_info(f"Removing static routes for {ctx.public_ip}/32...")
        ps_del = f"Get-NetRoute -DestinationPrefix '{ctx.public_ip}/32' -ErrorAction SilentlyContinue | Remove-NetRoute -Confirm:$false"
        subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_del], capture_output=True)

class ClusterSyncManager:
    """Orchestrates dynamic public IP discovery and propagation to K3s & Dune Game Server."""

    def __init__(self, ctx: TopologyContext):
        self.ctx = ctx
        self.ssh = SshExecutor(ctx.vm_user, ctx.vm_ip)

    def read_configured_external_ip(self) -> Optional[str]:
        cmd = f"sed -n '4p' {self.ctx.settings_file} 2>/dev/null"
        code, out = self.ssh.run(cmd)
        if code == 0 and out.strip():
            candidate = out.strip().splitlines()[-1]
            try:
                ipaddress.IPv4Address(candidate)
                return candidate
            except ValueError:
                return candidate
        return None

    def discover_k8s_resources(self):
        cmd = "sudo k3s kubectl get namespaces -o json"
        code, out = self.ssh.run(cmd)
        if code == 0:
            try:
                data = json.loads(out)
                for item in data.get("items", []):
                    ns_name = item.get("metadata", {}).get("name", "")
                    if ns_name.startswith("funcom-seabass-"):
                        self.ctx.k8s_namespace = ns_name
                        break
            except Exception:
                pass

        if self.ctx.k8s_namespace:
            cmd_deploy = f"sudo k3s kubectl get deployments -n {self.ctx.k8s_namespace} -o json"
            code, out_dep = self.ssh.run(cmd_deploy)
            if code == 0:
                try:
                    data = json.loads(out_dep)
                    for item in data.get("items", []):
                        d_name = item.get("metadata", {}).get("name", "")
                        if "sgw-deploy" in d_name:
                            self.ctx.k8s_gateway_deploy = d_name
                            break
                except Exception:
                    pass

    def synchronize_cluster_ip(self, target_ip: str) -> bool:
        configured_ip = self.read_configured_external_ip()
        if configured_ip == target_ip:
            print_success(f"Cluster external IP is consistent with WAN ({target_ip}). No cluster restart required.")
            return True

        print_warning(f"WAN IP change detected: Cluster={configured_ip} -> Actual WAN={target_ip}")
        print_info(f"Atomically updating {self.ctx.settings_file}...")

        update_cmd = (
            f"sh -c \""
            f"file='{self.ctx.settings_file}'; "
            f"tmp='{self.ctx.settings_file}.tmp'; "
            f"l1=\\$(sed -n '1p' \\$file 2>/dev/null); "
            f"l2=\\$(sed -n '2p' \\$file 2>/dev/null); "
            f"l3=\\$(sed -n '3p' \\$file 2>/dev/null); "
            f"printf '%s\\n%s\\n%s\\n{target_ip}\\n' \\\"\\$l1\\\" \\\"\\$l2\\\" \\\"\\$l3\\\" > \\$tmp && "
            f"mv \\$tmp \\$file\""
        )
        code, out = self.ssh.run(update_cmd)
        if code != 0:
            print_error(f"Failed to update settings file: {out}")
            return False

        print_info("Restarting K3s service to update node external IP...")
        code, out = self.ssh.run("sudo rc-service k3s restart")
        if code != 0:
            print_error(f"Failed to restart K3s: {out}")
            return False

        print_info("Waiting for K3s node external IP to register...")
        node_synced = False
        for _ in range(15):
            time.sleep(2)
            code, out = self.ssh.run("sudo k3s kubectl get node -o json")
            if code == 0:
                try:
                    data = json.loads(out)
                    items = data.get("items", [])
                    if items:
                        addresses = items[0].get("status", {}).get("addresses", [])
                        for addr in addresses:
                            if addr.get("type") == "ExternalIP" and addr.get("address") == target_ip:
                                node_synced = True
                                break
                except Exception:
                    pass
            if node_synced:
                break

        if not node_synced:
            print_warning("K3s node did not register new ExternalIP within timeout. Proceeding with pod refresh.")
        else:
            print_success(f"K3s node updated successfully: ExternalIP={target_ip}")

        self.discover_k8s_resources()
        if not self.ctx.k8s_namespace:
            print_warning("Battlegroup namespace not found yet. Skipping pod restarts.")
            return True

        if self.ctx.k8s_gateway_deploy:
            print_info(f"Restarting Gateway Deployment ({self.ctx.k8s_gateway_deploy})...")
            self.ssh.run(f"sudo k3s kubectl rollout restart deployment {self.ctx.k8s_gateway_deploy} -n {self.ctx.k8s_namespace}")

        print_info("Cycling game server pods to ensure re-registration in PostgreSQL...")
        self.ssh.run(
            f"sudo k3s kubectl get pods -n {self.ctx.k8s_namespace} -o name | "
            f"grep -E 'sg-survival|sg-overmap' | xargs -r sudo k3s kubectl delete -n {self.ctx.k8s_namespace} --wait=false"
        )
        return True

class LinuxNatManager:
    def __init__(self, ctx: TopologyContext):
        self.ctx = ctx
        self.ssh = SshExecutor(ctx.vm_user, ctx.vm_ip)

    def sanitize_and_sync(self) -> bool:
        print_info(f"Sanitizing iptables and applying network policy on VM ({self.ctx.vm_ip})...")

        code, dump = self.ssh.run("sudo iptables -t nat -S")
        if code != 0:
            print_error(f"SSH connection failed: {dump}")
            return False

        cleanup_commands: List[str] = []
        for line in dump.splitlines():
            line = line.strip()
            if line.startswith("-A POSTROUTING") and "DUNE-POST" not in line:
                if any(x in line for x in [self.ctx.vm_ip, self.ctx.subnet_cidr]):
                    cleanup_commands.append(f"sudo iptables -t nat -D {line[3:]}")

        chain_commands = [
            "sudo iptables -t nat -N DUNE-NAT 2>/dev/null || true",
            "sudo iptables -t nat -F DUNE-NAT",
            "sudo iptables -t nat -C PREROUTING -j DUNE-NAT 2>/dev/null || sudo iptables -t nat -I PREROUTING 1 -j DUNE-NAT",
            "sudo iptables -t nat -N DUNE-POST 2>/dev/null || true",
            "sudo iptables -t nat -F DUNE-POST",
            "sudo iptables -t nat -C POSTROUTING -j DUNE-POST 2>/dev/null || sudo iptables -t nat -I POSTROUTING 1 -j DUNE-POST",
            f"sudo iptables -t nat -A DUNE-NAT -d {self.ctx.public_ip} -p udp -m multiport --dports {self.ctx.game_udp_ports} -j DNAT --to-destination {self.ctx.vm_ip}"
        ]

        if self.ctx.is_same_subnet:
            print_success(f"L2 topology detected ({self.ctx.subnet_cidr}). Injecting self-loopback protected UDP+TCP SNAT.")
            chain_commands.append(f"sudo iptables -t nat -A DUNE-POST -s {self.ctx.vm_ip} -j RETURN")
            chain_commands.append(
                f"sudo iptables -t nat -A DUNE-POST -s {self.ctx.subnet_cidr} -d {self.ctx.vm_ip} -p udp -m multiport --dports {self.ctx.game_udp_ports} -j MASQUERADE"
            )
            chain_commands.append(
                f"sudo iptables -t nat -A DUNE-POST -s {self.ctx.subnet_cidr} -p tcp -m multiport --dports {self.ctx.node_tcp_ports} -j MASQUERADE"
            )
        else:
            print_info(f"L3 routed topology detected (Host {self.ctx.pc_ip} outside VM subnet {self.ctx.subnet_cidr}). Hairpin SNAT omitted.")

        full_script = " && ".join(cleanup_commands + chain_commands)
        ret, output = self.ssh.run(full_script)
        if ret == 0:
            print_success("Kernel network policy applied successfully.")
            return True
        else:
            print_error(f"Execution error on VM: {output}")
            return False

    def clean(self) -> bool:
        print_info("Flushing Dune NAT rules from VM...")
        cmd = (
            "sudo iptables -t nat -D PREROUTING -j DUNE-NAT 2>/dev/null || true && "
            "sudo iptables -t nat -F DUNE-NAT 2>/dev/null || true && "
            "sudo iptables -t nat -X DUNE-NAT 2>/dev/null || true && "
            "sudo iptables -t nat -D POSTROUTING -j DUNE-POST 2>/dev/null || true && "
            "sudo iptables -t nat -F DUNE-POST 2>/dev/null || true && "
            "sudo iptables -t nat -X DUNE-POST 2>/dev/null || true"
        )
        ret, _ = self.ssh.run(cmd)
        return ret == 0


class VerificationSuite:
    @staticmethod
    def verify(ctx: TopologyContext) -> bool:
        print(f"\n{Color.CYAN}==================================================={Color.RESET}")
        print(f"{Color.CYAN}  Dune: Awakening - Verification Suite (v2.5.0)    {Color.RESET}")
        print(f"{Color.CYAN}==================================================={Color.RESET}\n")

        assertions: List[Tuple[str, str, bool, str]] = []

        ps_query = (
            f"Get-NetRoute -DestinationPrefix '{ctx.public_ip}/32' -InterfaceAlias '{ctx.switch_alias}' "
            "-ErrorAction SilentlyContinue | Where-Object { $_.NextHop -eq '" + ctx.vm_ip + "' -and $_.RouteMetric -eq 1 } | "
            "ConvertTo-Json"
        )
        route_ok = NetworkDiscovery.run_powershell_json(ps_query) is not None
        assertions.append((
            "Windows Routing",
            f"Static route {ctx.public_ip}/32 -> {ctx.vm_ip} (Metric: 1)",
            route_ok,
            f"Active on {ctx.switch_alias}" if route_ok else "Route missing or attributes mismatched"
        ))

        ssh = SshExecutor(ctx.vm_user, ctx.vm_ip)
        code, out = ssh.run("sudo iptables -V")
        sudo_ok = (code == 0 and "iptables" in out)
        assertions.append((
            "Linux SSH & Sudo",
            "Passwordless execution of iptables",
            sudo_ok,
            out.strip() if sudo_ok else "SSH access or sudo authentication failed"
        ))

        if sudo_ok:
            code, nat_dump = ssh.run("sudo iptables -t nat -S")
            lines = [l.strip() for l in nat_dump.splitlines()]

            hook_pre = any(l.startswith("-A PREROUTING") and "-j DUNE-NAT" in l for l in lines)
            hook_post = any(l.startswith("-A POSTROUTING") and "-j DUNE-POST" in l for l in lines)
            assertions.append(("iptables PREROUTING", "Chain hook: PREROUTING -> DUNE-NAT", hook_pre, "Hook present" if hook_pre else "Missing hook"))
            assertions.append(("iptables POSTROUTING", "Chain hook: POSTROUTING -> DUNE-POST", hook_post, "Hook present" if hook_post else "Missing hook"))

            dnat_udp = any(
                "-A DUNE-NAT" in l and f"-d {ctx.public_ip}" in l and "-p udp" in l and ctx.game_udp_ports in l and f"--to-destination {ctx.vm_ip}" in l
                for l in lines
            )
            assertions.append(("iptables UDP DNAT", f"Forward UDP {ctx.game_udp_ports} to {ctx.vm_ip}", dnat_udp, "Verified" if dnat_udp else "Missing UDP DNAT"))

            no_tcp_dnat = not any("-A DUNE-NAT" in l and "-p tcp" in l and "-j DNAT" in l for l in lines)
            assertions.append(("iptables TCP Transparency", "Bypass TCP DNAT for Kube-Proxy NodePorts", no_tcp_dnat, "Clean" if no_tcp_dnat else "Conflicting TCP DNAT found"))

            if ctx.is_same_subnet:
                return_rule = any(f"-A DUNE-POST -s {ctx.vm_ip}/32 -j RETURN" in l or f"-A DUNE-POST -s {ctx.vm_ip} -j RETURN" in l for l in lines)
                masq_udp = any("-A DUNE-POST" in l and f"-s {ctx.subnet_cidr}" in l and f"-d {ctx.vm_ip}" in l and "-p udp" in l and "-j MASQUERADE" in l for l in lines)
                masq_tcp = any("-A DUNE-POST" in l and f"-s {ctx.subnet_cidr}" in l and "-p tcp" in l and ctx.node_tcp_ports in l and "-j MASQUERADE" in l for l in lines)
                assertions.append(("iptables Hairpin UDP", "MASQUERADE for UDP", masq_udp, "Verified" if masq_udp else "Missing UDP SNAT"))
                assertions.append(("iptables Hairpin TCP", f"MASQUERADE for TCP {ctx.node_tcp_ports}", masq_tcp, "Verified" if masq_tcp else "Missing TCP SNAT"))
                assertions.append(("iptables Loopback Guard", "RETURN for self-traffic", return_rule, "Verified" if return_rule else "Missing loopback guard"))

        # Cluster IP Consistency Assertion
        sync_mgr = ClusterSyncManager(ctx)
        configured_ip = sync_mgr.read_configured_external_ip()
        cluster_ip_ok = (configured_ip == ctx.public_ip)
        assertions.append((
            "Cluster IP Consistency",
            f"settings.conf External IP matches WAN ({ctx.public_ip})",
            cluster_ip_ok,
            f"Cluster IP: {configured_ip}" if cluster_ip_ok else f"Mismatch: settings.conf={configured_ip} vs WAN={ctx.public_ip}"
        ))

        failures = 0
        for component, test, success, details in assertions:
            if success:
                tag = f"{Color.GREEN}[PASS]{Color.RESET}"
            else:
                tag = f"{Color.RED}[FAIL]{Color.RESET}"
                failures += 1
            print(f"{tag} {component} :: {test}")
            print(f"       Details: {Color.GRAY}{details}{Color.RESET}")

        print(f"\n{Color.CYAN}==================================================={Color.RESET}")
        if failures == 0:
            print(f"  STATUS: {Color.GREEN}CONSISTENT (All verification checks passed){Color.RESET}")
            print(f"{Color.CYAN}==================================================={Color.RESET}\n")
            return True
        else:
            print(f"  STATUS: {Color.RED}INCONSISTENT ({failures} assertion failures){Color.RESET}")
            print(f"{Color.CYAN}==================================================={Color.RESET}\n")
            return False


def main():
    parser = argparse.ArgumentParser(description="Dune: Awakening Generalized Network & NAT Engine")
    parser.add_argument("action", choices=["sync", "verify", "clean"], help="Operation to execute")
    parser.add_argument("--config", default="config.json", help="Path to configuration file")
    args = parser.parse_args()

    try:
        ctx = NetworkDiscovery.build_context(args.config)
        if ctx is None:
            sys.exit(0)
        print_success(f"Topology: Public IP={ctx.public_ip}, Host PC={ctx.pc_ip}, VM={ctx.vm_ip}, Subnet={ctx.subnet_cidr} (Same Subnet: {ctx.is_same_subnet})")
    except Exception as e:
        print_error(f"Discovery initialization failure: {e}")
        sys.exit(1)

    cluster_mgr = ClusterSyncManager(ctx)
    nat_mgr = LinuxNatManager(ctx)

    if args.action == "sync":
        c_ok = cluster_mgr.synchronize_cluster_ip(ctx.public_ip)
        w_ok = RouteManager.sync_windows_route(ctx)
        l_ok = nat_mgr.sanitize_and_sync()
        if c_ok and w_ok and l_ok:
            print(f"\n{Color.GREEN}[SUCCESS] Synchronization complete.{Color.RESET}")
            sys.exit(0)
        sys.exit(1)

    elif args.action == "verify":
        success = VerificationSuite.verify(ctx)
        sys.exit(0 if success else 1)

    elif args.action == "clean":
        RouteManager.clean_windows_route(ctx)
        nat_mgr.clean()
        print(f"\n{Color.GREEN}[SUCCESS] Environment cleaned.{Color.RESET}")
        sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print_error(f"Unhandled Python exception: {e}")
        traceback.print_exc()
        sys.exit(1)