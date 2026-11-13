"""Configuration from environment variables (see .env.example)."""
import os
import secrets
from dataclasses import dataclass, field


def _bool(name, default):
    v = os.environ.get(name)
    if v is None or v == '':
        return default
    return v.strip().lower() in ('1', 'true', 'yes', 'on')


def _int(name, default):
    v = os.environ.get(name)
    return int(v) if v not in (None, '') else default


def _str(name, default=''):
    return os.environ.get(name, default) or default


@dataclass
class Config:
    # BMC
    bmc_host: str = ''
    bmc_user: str = 'ADMIN'
    bmc_password: str = field(default='', repr=False)
    bmc_web_scheme: str = 'http'
    bmc_web_port: int = 0                 # 0 = default port of the scheme
    bmc_kvm_port: int = 0                 # 0 = the one announced by the BMC (usually 5900)
    # IPMI
    ipmi_backend: str = 'native'          # 'native' (pyghmi, pure Python) or 'ipmitool'
    ipmitool: str = 'ipmitool'
    ipmi_interface: str = 'lanplus'
    ipmi_cipher: str = '3'                # X9 boards do not support suite 17 (the ipmitool 1.8.19 default)
    ipmi_interval: int = 30
    sel_interval: int = 300
    ipmi_timeout: int = 20
    # web gateway
    listen: str = '0.0.0.0'
    port: int = 8080
    web_user: str = 'admin'
    web_password_hash: str = field(default='', repr=False)
    web_password: str = field(default='', repr=False)   # plain-text alternative (not recommended)
    session_hours: int = 12
    metrics_token: str = field(default='', repr=False)
    metrics_public: bool = False
    power_control: bool = True
    trusted_proxy: bool = False           # trust X-Forwarded-For / X-Forwarded-Proto
    # KVM
    kvm_idle_timeout: int = 300
    kvm_internal_port: int = 5901
    vnc_enable: bool = True
    vnc_listen: str = '0.0.0.0'
    vnc_port: int = 5900
    vnc_password: str = field(default='', repr=False)
    # history
    data_dir: str = '/data'
    retention_days: int = 7
    log_level: str = 'INFO'

    @classmethod
    def from_env(cls):
        c = cls(
            bmc_host=_str('BMC_HOST'),
            bmc_user=_str('BMC_USER', 'ADMIN'),
            bmc_password=_str('BMC_PASSWORD'),
            bmc_web_scheme=_str('BMC_WEB_SCHEME', 'http'),
            bmc_web_port=_int('BMC_WEB_PORT', 0),
            bmc_kvm_port=_int('BMC_KVM_PORT', 0),
            ipmi_backend=_str('IPMI_BACKEND', 'native').lower(),
            ipmitool=_str('IPMITOOL', 'ipmitool'),
            ipmi_interface=_str('IPMI_INTERFACE', 'lanplus'),
            ipmi_cipher=_str('IPMI_CIPHER', '3'),
            ipmi_interval=_int('IPMI_INTERVAL', 30),
            sel_interval=_int('SEL_INTERVAL', 300),
            ipmi_timeout=_int('IPMI_TIMEOUT', 20),
            listen=_str('WEBKVM_LISTEN', '0.0.0.0'),
            port=_int('WEBKVM_PORT', 8080),
            web_user=_str('WEBKVM_USER', 'admin'),
            web_password_hash=_str('WEBKVM_PASSWORD_HASH'),
            web_password=_str('WEBKVM_PASSWORD'),
            session_hours=_int('WEBKVM_SESSION_HOURS', 12),
            metrics_token=_str('WEBKVM_METRICS_TOKEN'),
            metrics_public=_bool('WEBKVM_METRICS_PUBLIC', False),
            power_control=_bool('WEBKVM_POWER_CONTROL', True),
            trusted_proxy=_bool('WEBKVM_TRUSTED_PROXY', False),
            kvm_idle_timeout=_int('KVM_IDLE_TIMEOUT', 300),
            kvm_internal_port=_int('KVM_INTERNAL_PORT', 5901),
            vnc_enable=_bool('VNC_ENABLE', True),
            vnc_listen=_str('VNC_LISTEN', '0.0.0.0'),
            vnc_port=_int('VNC_PORT', 5900),
            vnc_password=_str('VNC_PASSWORD'),
            data_dir=_str('DATA_DIR', '/data'),
            retention_days=_int('RETENTION_DAYS', 7),
            log_level=_str('LOG_LEVEL', 'INFO'),
        )
        return c

    def validate(self):
        """Return (errors, warnings)."""
        errors, warnings = [], []
        if not self.bmc_host:
            errors.append('BMC_HOST is not set')
        if not self.bmc_password:
            errors.append('BMC_PASSWORD is not set')
        if not self.web_password_hash and not self.web_password:
            errors.append('set WEBKVM_PASSWORD_HASH (python -m webkvm hash-password) '
                          'or WEBKVM_PASSWORD')
        elif self.web_password and not self.web_password_hash:
            warnings.append('WEBKVM_PASSWORD is in plain text: prefer WEBKVM_PASSWORD_HASH')
        if self.vnc_enable and not self.vnc_password:
            warnings.append('VNC_ENABLE is on but VNC_PASSWORD is empty: native VNC port disabled')
            self.vnc_enable = False
        if self.vnc_password and len(self.vnc_password) > 8:
            warnings.append('VNC_PASSWORD is longer than 8 characters: the VNC protocol only uses the first 8')
        if self.ipmi_backend not in ('native', 'ipmitool'):
            errors.append(f'invalid IPMI_BACKEND: {self.ipmi_backend} (native or ipmitool)')
        if self.metrics_public and self.metrics_token:
            warnings.append('WEBKVM_METRICS_PUBLIC ignored: WEBKVM_METRICS_TOKEN is set')
        return errors, warnings


def new_token():
    return secrets.token_urlsafe(32)
