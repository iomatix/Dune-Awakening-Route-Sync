"""
Dune: Awakening - Generalized Network Routing & NAT Engine (Version 2.1.2)
Self-contained, deterministic topology and firewall management with ANSI colors and crash guard.
"""

import argparse
import ipaddress
import json
import os
import socket
import subprocess
import sys
import traceback
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

# Enable Virtual Terminal Processing for ANSI colors on Windows console
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
    print(f"{Color.YELLOW}[!] WARNING: {msg}{Color.RESET}")


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


class NetworkDiscovery:
    @staticmethod
    def query_public_ip() -> str:
        url = "https://api.ipify.org"
        req = urllib.request.Request(url, headers={"User-Agent": "DuneNetEngine/2.1"})
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.read().decode("utf-8").strip()

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
    def build_context(cls, config_path: str) -> TopologyContext:
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

        switch_alias, pc_ip, prefix_len = cls.resolve_host_interface(preferred_switch)
        if configured_pc_ip:
            pc_ip = configured_pc_ip
        if not pc_ip or not prefix_len:
            raise RuntimeError("Failed to discover active host IP and network prefix length.")

        host_net = ipaddress.IPv4Network(f"{pc_ip}/{prefix_len}", strict=False)

        vm_ip = configured_vm_ip or cls.resolve_vm_ip(switch_alias, pc_ip, host_net)
        if not vm_ip:
            raise RuntimeError(f"Failed to resolve VM IP on switch '{switch_alias}'. Verify that VM is booted.")

        public_ip = cls.query_public_ip()

        vm_obj = ipaddress.IPv4Address(vm_ip)
        is_same_subnet = vm_obj in host_net
        subnet_cidr = str(host_net)

        game_ports = config.get("game_udp_ports") or "7777:7810"
        node_ports = config.get("node_tcp_ports") or "30000:32767"

        return TopologyContext(
            public_ip=public_ip,
            vm_ip=vm_ip,
            pc_ip=pc_ip,
            vm_user=vm_user,
            switch_alias=switch_alias,
            is_same_subnet=is_same_subnet,
            subnet_cidr=subnet_cidr,
            game_udp_ports=game_ports,
            node_tcp_ports=node_ports
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
                if any(x in line for x in ["7777:7810", self.ctx.vm_ip, self.ctx.subnet_cidr]):
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
            print_success(f"L2 topology detected ({self.ctx.subnet_cidr}). Injecting self-loopback protected UDP SNAT.")
            chain_commands.append(f"sudo iptables -t nat -A DUNE-POST -s {self.ctx.vm_ip} -j RETURN")
            chain_commands.append(
                f"sudo iptables -t nat -A DUNE-POST -s {self.ctx.subnet_cidr} -d {self.ctx.vm_ip} -p udp -m multiport --dports {self.ctx.game_udp_ports} -j MASQUERADE"
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
        print(f"{Color.CYAN}  Dune: Awakening - Verification Suite (English)   {Color.RESET}")
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
            code_ip, lo_dump = ssh.run("ip addr show dev lo")
            lo_has_public = (code_ip == 0 and f"{ctx.public_ip}/32" in lo_dump)
           
            code, nat_dump = ssh.run("sudo iptables -t nat -S")
            lines = [l.strip() for l in nat_dump.splitlines()]

            hook_pre = any(l.startswith("-A PREROUTING") and "-j DUNE-NAT" in l for l in lines)
            hook_post = any(l.startswith("-A POSTROUTING") and "-j DUNE-POST" in l for l in lines)
            assertions.append(("iptables PREROUTING", "Chain hook: PREROUTING -> DUNE-NAT", hook_pre, "Hook present" if hook_pre else "Missing hook"))
            assertions.append(("iptables POSTROUTING", "Chain hook: POSTROUTING -> DUNE-POST", hook_post, "Hook present" if hook_post else "Missing hook"))

            dnat_match = any(
                "-A DUNE-NAT" in l and f"-d {ctx.public_ip}" in l and "-p udp" in l and ctx.game_udp_ports in l and f"--to-destination {ctx.vm_ip}" in l
                for l in lines
            )
            assertions.append((
                "iptables DNAT",
                f"Forward UDP {ctx.game_udp_ports} to {ctx.vm_ip}",
                dnat_match,
                "Rule verified" if dnat_match else "Rule missing or parameters mismatched"
            ))

            if ctx.is_same_subnet:
                return_rule = any(f"-A DUNE-POST -s {ctx.vm_ip}/32 -j RETURN" in l or f"-A DUNE-POST -s {ctx.vm_ip} -j RETURN" in l for l in lines)
                masq_rule = any("-A DUNE-POST" in l and f"-s {ctx.subnet_cidr}" in l and f"-d {ctx.vm_ip}" in l and "-p udp" in l and "-j MASQUERADE" in l for l in lines)
                snat_ok = return_rule and masq_rule
                assertions.append(("iptables Hairpin", "Scoped UDP SNAT (RETURN Guard & MASQUERADE)", snat_ok, "Rules verified" if snat_ok else "Missing guard or MASQUERADE rule"))
            else:
                assertions.append(("iptables Hairpin", "Bypass L2 SNAT (Routed L3 topology)", True, "Omitted intentionally"))

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
        print_success(f"Topology: Public IP={ctx.public_ip}, Host PC={ctx.pc_ip}, VM={ctx.vm_ip}, Subnet={ctx.subnet_cidr} (Same Subnet: {ctx.is_same_subnet})")
    except Exception as e:
        print_error(f"Discovery initialization failure: {e}")
        sys.exit(1)

    nat_mgr = LinuxNatManager(ctx)

    if args.action == "sync":
        w_ok = RouteManager.sync_windows_route(ctx)
        l_ok = nat_mgr.sanitize_and_sync()
        if w_ok and l_ok:
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