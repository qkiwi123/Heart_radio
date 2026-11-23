"""Read-only PC connection indicators for the local dashboard."""
import asyncio
import threading
import time


REFRESH_INTERVAL_SECONDS = 1
MAX_SNAPSHOT_AGE_SECONDS = 10


def read_network():
    from winrt.windows.networking.connectivity import NetworkConnectivityLevel, NetworkInformation

    profile = NetworkInformation.get_internet_connection_profile()
    if profile is None:
        # Also show a local-only connection when there is no preferred Internet route.
        profiles = (p for p in NetworkInformation.get_connection_profiles()
                    if p.get_network_connectivity_level() != NetworkConnectivityLevel.NONE)
        profile = max(profiles, default=None, key=lambda p: (
            p.get_network_connectivity_level(), p.is_wlan_connection_profile))
    if profile is None:
        return dict(state="disconnected")

    if profile.is_wlan_connection_profile:
        try:
            details = profile.wlan_connection_profile_details
            name = details.get_connected_ssid() if details else None
        except (OSError, RuntimeError):
            name = None
        kind = "wifi"
    else:
        adapter = profile.network_adapter
        kind = "ethernet" if adapter and adapter.iana_interface_type == 6 else "other"
        name = profile.profile_name
    return dict(state="connected", kind=kind, name=name or None)


async def read_bluetooth():
    from winrt.windows.devices.radios import Radio, RadioKind, RadioState

    radios = await asyncio.wait_for(Radio.get_radios_async(), timeout=4)
    states = {radio.state for radio in radios if radio.kind == RadioKind.BLUETOOTH}
    if not states:
        return "unavailable"
    if RadioState.ON in states:
        return "on"
    if states <= {RadioState.OFF, RadioState.DISABLED}:
        return "disabled" if states == {RadioState.DISABLED} else "off"
    return "unknown"


class HostConnectivity:
    def __init__(self):
        self.lock = threading.Lock()
        self.refreshing = False
        self.updated = None
        self.value = None

    def snapshot(self):
        with self.lock:
            age = time.monotonic() - self.updated if self.updated is not None else float("inf")
            if age >= REFRESH_INTERVAL_SECONDS and not self.refreshing:
                self.refreshing = True
                threading.Thread(target=self._refresh, name="host-connectivity", daemon=True).start()
            if age < MAX_SNAPSHOT_AGE_SECONDS:
                return self.value
            return dict(network=dict(state="loading"), bluetooth="loading")

    def _refresh(self):
        network, bluetooth = dict(state="unknown"), "unknown"
        try:
            try:
                network = read_network()
            except (ImportError, OSError, RuntimeError):
                pass
            try:
                bluetooth = asyncio.run(read_bluetooth())
            except (ImportError, OSError, RuntimeError, TimeoutError):
                pass
        finally:
            with self.lock:
                self.value = dict(network=network, bluetooth=bluetooth)
                self.updated = time.monotonic()
                self.refreshing = False
