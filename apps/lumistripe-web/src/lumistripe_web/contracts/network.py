"""Wi-Fi adapter and NetworkManager request/response contracts."""

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class NetworkAdapterInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    interface: str
    state: str
    connection: str | None = None
    current_ssid: str | None = None
    hotspot: bool | None
    connectable: bool
    reason: str | None = None


class NetworkStatusResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    manager_available: bool
    error: str | None = None
    adapters: tuple[NetworkAdapterInfo, ...] = ()


class WifiNetworkInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    ssid: str
    security: str
    signal: int = Field(ge=0, le=100)
    secured: bool


class WifiScanRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    interface: str = Field(min_length=1, max_length=15)


class WifiScanResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    interface: str
    networks: tuple[WifiNetworkInfo, ...] = ()


class WifiConnectRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    interface: str = Field(min_length=1, max_length=15)
    ssid: str = Field(min_length=1, max_length=32)
    password: SecretStr = Field(default=SecretStr(""), max_length=63)


class WifiConnectResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    interface: str
    ssid: str
    connected: bool


__all__ = [
    "NetworkAdapterInfo",
    "NetworkStatusResponse",
    "WifiConnectRequest",
    "WifiConnectResponse",
    "WifiNetworkInfo",
    "WifiScanRequest",
    "WifiScanResponse",
]
