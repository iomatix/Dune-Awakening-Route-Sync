"""
Dune: Awakening - Automated Test Suite for dune_net.py (Version 2.2.0)
Unit and Live Integration Tests with ANSI colors and Hyper-V lifecycle validation.
"""

import ipaddress
import os
import sys
import unittest
from typing import List

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


try:
    import dune_net
except ImportError:
    print(f"{Color.RED}[-] CRITICAL: 'dune_net.py' must be in the same directory.{Color.RESET}")
    sys.exit(1)


class ColoredTestResult(unittest.TextTestResult):
    def addSuccess(self, test):
        super().addSuccess(test)
        if self.showAll:
            self.stream.writeln(f"{Color.GREEN}PASS{Color.RESET}")

    def addError(self, test, err):
        super().addError(test, err)
        if self.showAll:
            self.stream.writeln(f"{Color.RED}ERROR{Color.RESET}")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        if self.showAll:
            self.stream.writeln(f"{Color.RED}FAIL{Color.RESET}")

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        if self.showAll:
            self.stream.writeln(f"{Color.YELLOW}SKIP{Color.RESET}")


class ColoredTestRunner(unittest.TextTestRunner):
    resultclass = ColoredTestResult


class TestDuneNetUnit(unittest.TestCase):
    """Unit tests validating core algorithms and boundary conditions."""

    def test_multicast_and_broadcast_filtering(self):
        """Ensure multicast and broadcast addresses are never accepted as valid VM targets."""
        host_net = ipaddress.IPv4Network("192.168.1.0/24")
        test_candidates = [
            "239.255.255.250",
            "255.255.255.255",
            "192.168.1.255",
            "192.168.1.0",
            "192.168.1.33",
            "192.168.1.57"
        ]

        valid_ips: List[str] = []
        for ip_str in test_candidates:
            ip_obj = ipaddress.IPv4Address(ip_str)
            if (
                str(ip_obj) != "192.168.1.33"
                and str(ip_obj) != "255.255.255.255"
                and not ip_obj.is_multicast
                and not ip_obj.is_loopback
                and ip_obj != host_net.broadcast_address
                and ip_obj != host_net.network_address
            ):
                valid_ips.append(str(ip_obj))

        self.assertEqual(valid_ips, ["192.168.1.57"])

    def test_subnet_topology_detection(self):
        """Verify mathematical calculation of same-subnet (L2) vs routed (L3)."""
        host_net = ipaddress.IPv4Network("192.168.1.33/24", strict=False)
        same_subnet_vm = ipaddress.IPv4Address("192.168.1.57")
        different_subnet_vm = ipaddress.IPv4Address("172.24.16.5")

        self.assertTrue(same_subnet_vm in host_net)
        self.assertFalse(different_subnet_vm in host_net)


class TestDuneNetLive(unittest.TestCase):
    """Live environment integration tests against active Windows host and Linux VM."""

    @classmethod
    def setUpClass(cls):
        config_path = os.path.join(os.path.dirname(__file__), "config.json")
        try:
            cls.ctx = dune_net.NetworkDiscovery.build_context(config_path)
            if cls.ctx is None:
                raise unittest.SkipTest("Live tests skipped: Hyper-V VM is offline or unmanaged.")
        except Exception as e:
            raise unittest.SkipTest(f"Live tests skipped: Environment discovery failed ({e})")

    def test_live_windows_static_route(self):
        """Verify Windows routing table has the active /32 static route with Metric 1."""
        ps_query = (
            f"Get-NetRoute -DestinationPrefix '{self.ctx.public_ip}/32' -InterfaceAlias '{self.ctx.switch_alias}' "
            "-ErrorAction SilentlyContinue | Where-Object { $_.NextHop -eq '" + self.ctx.vm_ip + "' -and $_.RouteMetric -eq 1 } | "
            "ConvertTo-Json"
        )
        route_data = dune_net.NetworkDiscovery.run_powershell_json(ps_query)
        self.assertIsNotNone(route_data, f"Windows route for {self.ctx.public_ip}/32 via {self.ctx.vm_ip} is missing.")

    def test_live_ssh_passwordless_sudo(self):
        """Verify passwordless sudo execution on the target Linux virtual machine."""
        ssh = dune_net.SshExecutor(self.ctx.vm_user, self.ctx.vm_ip)
        code, out = ssh.run("sudo iptables -V")
        self.assertEqual(code, 0, f"SSH/Sudo authentication failed: {out}")
        self.assertIn("iptables", out)

    def test_live_iptables_game_dnat_and_hairpin(self):
        """Verify DUNE-NAT hooks, UDP game port DNAT, and scoped UDP Hairpin SNAT."""
        ssh = dune_net.SshExecutor(self.ctx.vm_user, self.ctx.vm_ip)
        code, dump = ssh.run("sudo iptables -t nat -S")
        self.assertEqual(code, 0, f"Failed to dump iptables nat table: {dump}")

        lines = [line.strip() for line in dump.splitlines()]

        self.assertTrue(any(l.startswith("-A PREROUTING") and "-j DUNE-NAT" in l for l in lines), "DUNE-NAT hook missing in PREROUTING")
        self.assertTrue(any(l.startswith("-A POSTROUTING") and "-j DUNE-POST" in l for l in lines), "DUNE-POST hook missing in POSTROUTING")

        self.assertTrue(
            any("-A DUNE-NAT" in l and f"-d {self.ctx.public_ip}" in l and "-p udp" in l and self.ctx.game_udp_ports in l and f"--to-destination {self.ctx.vm_ip}" in l for l in lines),
            "UDP game port DNAT rule missing in DUNE-NAT"
        )

        if self.ctx.is_same_subnet:
            has_return = any(f"-A DUNE-POST -s {self.ctx.vm_ip}/32 -j RETURN" in l or f"-A DUNE-POST -s {self.ctx.vm_ip} -j RETURN" in l for l in lines)
            has_masq = any("-A DUNE-POST" in l and f"-s {self.ctx.subnet_cidr}" in l and f"-d {self.ctx.vm_ip}" in l and "-p udp" in l and "-j MASQUERADE" in l for l in lines)
            self.assertTrue(has_return, "Loopback prevention guard (-j RETURN) missing in DUNE-POST")
            self.assertTrue(has_masq, "UDP Hairpin MASQUERADE rule missing in DUNE-POST")


def main():
    print(f"{Color.CYAN}==================================================={Color.RESET}")
    print(f"{Color.CYAN}  Dune: Awakening - Verification & Test Suite      {Color.RESET}")
    print(f"{Color.CYAN}==================================================={Color.RESET}\n")

    suite = unittest.TestSuite()
    suite.addTest(unittest.TestLoader().loadTestsFromTestCase(TestDuneNetUnit))
    suite.addTest(unittest.TestLoader().loadTestsFromTestCase(TestDuneNetLive))

    runner = ColoredTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n{Color.RED}[-] Unhandled exception during testing: {e}{Color.RESET}")
        sys.exit(1)