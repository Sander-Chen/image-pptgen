"""Fail-closed Codex executable selection for product-owned child processes."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import platform
import re
import stat
import subprocess
import sys
from typing import Sequence


_OPENAI_TEAM_IDENTIFIER = "2DC432GLL2"
_OPENAI_CODEX_IDENTIFIERS = frozenset({"codex", "com.openai.codex"})
_MACOS_PRIMARY = Path("/Applications/Codex.app/Contents/Resources/codex")
_MACOS_BOUNDED_FALLBACK = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
_WINDOWS_PACKAGE_NAME = "OpenAI.Codex"
_WINDOWS_PACKAGE_FAMILY = "OpenAI.Codex_2p2nqsd0c76g0"
_WINDOWS_RESOURCE_RELATIVE = Path("app") / "resources" / "codex.exe"
_WINDOWS_CACHE_RELATIVE = Path("OpenAI") / "Codex" / "bin"
_IDENTITY_TOOL_TIMEOUT_SECONDS = 10
_ERROR_INSUFFICIENT_BUFFER = 122
_PACKAGE_FILTER_HEAD = 0x00000010
_PACKAGE_FILTER_DIRECT = 0x00000020
_PACKAGE_INFORMATION_FULL = 0x00000100
_PACKAGE_PROPERTY_RESOURCE = 0x00000002
_PROCESSOR_ARCHITECTURE_AMD64 = 9
_WTD_UI_NONE = 2
_WTD_REVOKE_NONE = 0
_WTD_CHOICE_FILE = 1
_WTD_STATEACTION_IGNORE = 0
_CERT_QUERY_OBJECT_FILE = 1
# CERT_QUERY_CONTENT_PKCS7_SIGNED_EMBED is enum value 10 in the Windows SDK;
# CryptQueryObject's content flag is the corresponding bit, not the enum value.
_CERT_QUERY_CONTENT_FLAG_PKCS7_SIGNED_EMBED = 1 << 10
_CERT_QUERY_FORMAT_FLAG_BINARY = 2
_CMSG_SIGNER_INFO_PARAM = 6
_X509_ASN_ENCODING = 0x00000001
_PKCS_7_ASN_ENCODING = 0x00010000
_CERT_FIND_SUBJECT_CERT = 0x000B0000
_CERT_X500_NAME_STR = 3
# Keep the native X.500 fields in the CN,O,L,S,C order captured by the
# existing exact signer allowlist.  Without this flag Windows returns its
# default C,S,L,O,CN order and a valid OpenAI signer is rejected.
_CERT_NAME_STR_REVERSE_FLAG = 0x02000000
_CERT_X500_SUBJECT_STR = _CERT_NAME_STR_REVERSE_FLAG | _CERT_X500_NAME_STR
_RO_INIT_MULTITHREADED = 1
_RPC_E_CHANGED_MODE = -2147417850
_AUTHENTICODE_SUBJECT_MAX_LENGTH = 512
# Microsoft win32metadata's recompiled Windows SDK IDL defines the manager
# interface/method order; Microsoft windows-rs generated Windows.winmd bindings
# confirm the Package3 and PackageStatus interface IDs and first-method slots.
_IPACKAGE_MANAGER_FIND_PACKAGE_BY_USER_SECURITY_ID_PACKAGE_FULL_NAME = 21
_IPACKAGE3_STATUS = 6
_IPACKAGE_STATUS_VERIFY_IS_OK = 6

# These are the complete Authenticode subject representations captured from
# accepted Windows Desktop managed-cache evidence. Normalize only presentation
# whitespace and case; do not dequote, parse loosely, or match a substring.
_OPENAI_AUTHENTICODE_SUBJECTS = frozenset(
    {
        "cn=openai opco, llc, o=openai opco, llc, l=san francisco, s=california, c=us",
        'cn="openai opco, llc", o="openai opco, llc", '
        "l=san francisco, s=california, c=us",
    }
)

class CodexExecutableUnavailable(RuntimeError):
    """No executable satisfies the platform's bounded Desktop identity contract."""

    def __init__(self, reason: str, *, path: Path | None = None) -> None:
        self.reason = reason
        self.path = path
        suffix = f": {path}" if path is not None else ""
        super().__init__(f"Codex Desktop executable unavailable: {reason}{suffix}")


def _windows_package_status_unavailable(
    hresult: int | None = None,
) -> CodexExecutableUnavailable:
    """Build the stable public error while retaining one bounded native detail."""
    error = CodexExecutableUnavailable("windows_package_status_unavailable")
    if hresult is not None:
        try:
            value = int(hresult)
        except (TypeError, ValueError, OverflowError):
            value = None
        if value is not None and -(1 << 31) <= value <= (1 << 32) - 1:
            setattr(error, "_hresult", value)
    return error


@dataclass(frozen=True)
class CodexExecutableIdentity:
    path: str
    source: str
    architectures: tuple[str, ...] = ()
    signer_identifier: str | None = None
    team_identifier: str | None = None
    package_family_name: str | None = None
    appx_package_full_name: str | None = None
    sha256: str | None = None
    signature_status: str | None = None
    version: str | None = None
    size: int | None = None
    publisher: str | None = None
    provenance_root: str | None = None
    appx_resource_path: str | None = None
    appx_resource_version: str | None = None
    appx_resource_size: int | None = None
    appx_resource_sha256: str | None = None


def _run_identity_tool(command: Sequence[str], *, path: Path) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=_IDENTITY_TOOL_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CodexExecutableUnavailable("identity_probe_failed", path=path) from exc
    if result.returncode != 0:
        raise CodexExecutableUnavailable("identity_probe_failed", path=path)
    return result


def _regular_executable(path: Path) -> bool:
    try:
        file_stat = path.lstat()
    except OSError:
        return False
    return stat.S_ISREG(file_stat.st_mode) and not path.is_symlink() and os.access(path, os.X_OK)


def _is_reparse_point(file_stat: os.stat_result) -> bool:
    attributes = getattr(file_stat, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _regular_file_without_reparse(path: Path) -> bool:
    try:
        file_stat = path.lstat()
    except OSError:
        return False
    return (
        stat.S_ISREG(file_stat.st_mode)
        and not path.is_symlink()
        and not _is_reparse_point(file_stat)
    )


def _directory_without_reparse(path: Path) -> bool:
    try:
        file_stat = path.lstat()
    except OSError:
        return False
    return (
        stat.S_ISDIR(file_stat.st_mode)
        and not path.is_symlink()
        and not _is_reparse_point(file_stat)
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise CodexExecutableUnavailable("candidate_hash_failed", path=path) from exc
    return digest.hexdigest()


def _normalize_authenticode_subject(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value.strip()).casefold()


def _bounded_windows_long(value: object) -> int | None:
    """Return a value only when it fits the signed 32-bit Windows LONG ABI."""
    if isinstance(value, bool):
        return None
    try:
        candidate = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not -(1 << 31) <= candidate <= (1 << 31) - 1:
        return None
    return candidate


def _is_trusted_openai_authenticode_subject(value: object) -> bool:
    return _normalize_authenticode_subject(value) in _OPENAI_AUTHENTICODE_SUBJECTS


class _PackageVersionParts(ctypes.Structure):
    _fields_ = [
        ("revision", ctypes.c_ushort),
        ("build", ctypes.c_ushort),
        ("minor", ctypes.c_ushort),
        ("major", ctypes.c_ushort),
    ]


class _PackageVersion(ctypes.Union):
    _fields_ = [("value", ctypes.c_ulonglong), ("parts", _PackageVersionParts)]


class _PackageId(ctypes.Structure):
    _fields_ = [
        ("reserved", ctypes.c_uint32),
        ("processor_architecture", ctypes.c_uint32),
        ("version", _PackageVersion),
        ("name", ctypes.c_void_p),
        ("publisher", ctypes.c_void_p),
        ("resource_id", ctypes.c_void_p),
        ("publisher_id", ctypes.c_void_p),
    ]


class _Guid(ctypes.Structure):
    _fields_ = [
        ("data1", ctypes.c_uint32),
        ("data2", ctypes.c_uint16),
        ("data3", ctypes.c_uint16),
        ("data4", ctypes.c_ubyte * 8),
    ]


_IID_IPACKAGE_MANAGER = _Guid(
    0x9A7D4B65,
    0x5E8F,
    0x4FC7,
    (ctypes.c_ubyte * 8)(0xA2, 0xE5, 0x7F, 0x69, 0x25, 0xCB, 0x8B, 0x53),
)
_IID_IPACKAGE3 = _Guid(
    0x5F738B61,
    0xF86A,
    0x4917,
    (ctypes.c_ubyte * 8)(0x93, 0xD1, 0xF1, 0xEE, 0x9D, 0x3B, 0x35, 0xD9),
)
_IID_IPACKAGE_STATUS = _Guid(
    0x5FE74F71,
    0xA365,
    0x4C09,
    (ctypes.c_ubyte * 8)(0xA0, 0x2D, 0x04, 0x6D, 0x52, 0x5E, 0xA1, 0xDA),
)


class _WintrustFileInfo(ctypes.Structure):
    _fields_ = [
        ("cb_struct", ctypes.c_uint32),
        ("path", ctypes.c_wchar_p),
        ("file_handle", ctypes.c_void_p),
        ("known_subject", ctypes.POINTER(_Guid)),
    ]


class _WintrustData(ctypes.Structure):
    _fields_ = [
        ("cb_struct", ctypes.c_uint32),
        ("policy_callback_data", ctypes.c_void_p),
        ("sip_client_data", ctypes.c_void_p),
        ("ui_choice", ctypes.c_uint32),
        ("revocation_checks", ctypes.c_uint32),
        ("union_choice", ctypes.c_uint32),
        ("file_info", ctypes.POINTER(_WintrustFileInfo)),
        ("state_action", ctypes.c_uint32),
        ("state_data", ctypes.c_void_p),
        ("url_reference", ctypes.c_wchar_p),
        ("provider_flags", ctypes.c_uint32),
        ("ui_context", ctypes.c_uint32),
        ("signature_settings", ctypes.c_void_p),
    ]


class _CryptDataBlob(ctypes.Structure):
    _fields_ = [("cb_data", ctypes.c_uint32), ("data", ctypes.POINTER(ctypes.c_ubyte))]


class _CryptAlgorithmIdentifier(ctypes.Structure):
    _fields_ = [("object_identifier", ctypes.c_char_p), ("parameters", _CryptDataBlob)]


class _CryptBitBlob(ctypes.Structure):
    _fields_ = [
        ("cb_data", ctypes.c_uint32),
        ("data", ctypes.POINTER(ctypes.c_ubyte)),
        ("unused_bits", ctypes.c_uint32),
    ]


class _CryptAttribute(ctypes.Structure):
    _fields_ = [
        ("object_identifier", ctypes.c_char_p),
        ("value_count", ctypes.c_uint32),
        ("values", ctypes.POINTER(_CryptDataBlob)),
    ]


class _CryptAttributes(ctypes.Structure):
    """WinCrypt CRYPT_ATTRIBUTES, embedded twice in CMSG_SIGNER_INFO."""

    _fields_ = [
        ("attribute_count", ctypes.c_uint32),
        ("attributes", ctypes.POINTER(_CryptAttribute)),
    ]


class _CertPublicKeyInfo(ctypes.Structure):
    _fields_ = [("algorithm", _CryptAlgorithmIdentifier), ("public_key", _CryptBitBlob)]


class _CertInfo(ctypes.Structure):
    _fields_ = [
        ("version", ctypes.c_uint32),
        ("serial_number", _CryptDataBlob),
        ("signature_algorithm", _CryptAlgorithmIdentifier),
        ("issuer", _CryptDataBlob),
        ("not_before_low", ctypes.c_uint32),
        ("not_before_high", ctypes.c_uint32),
        ("not_after_low", ctypes.c_uint32),
        ("not_after_high", ctypes.c_uint32),
        ("subject", _CryptDataBlob),
        ("subject_public_key_info", _CertPublicKeyInfo),
        ("issuer_unique_id", _CryptBitBlob),
        ("subject_unique_id", _CryptBitBlob),
        ("extension_count", ctypes.c_uint32),
        ("extensions", ctypes.c_void_p),
    ]


class _CmsgSignerInfo(ctypes.Structure):
    _fields_ = [
        ("cb_size", ctypes.c_uint32),
        ("issuer", _CryptDataBlob),
        ("serial_number", _CryptDataBlob),
        ("hash_algorithm", _CryptAlgorithmIdentifier),
        ("hash_encryption_algorithm", _CryptAlgorithmIdentifier),
        ("encrypted_hash", _CryptDataBlob),
        ("auth_attributes", _CryptAttributes),
        ("unauth_attributes", _CryptAttributes),
    ]


class _CertContext(ctypes.Structure):
    _fields_ = [
        ("encoding_type", ctypes.c_uint32),
        ("encoded", ctypes.POINTER(ctypes.c_ubyte)),
        ("encoded_size", ctypes.c_uint32),
        ("info", ctypes.POINTER(_CertInfo)),
        ("store", ctypes.c_void_p),
    ]


@dataclass(frozen=True)
class _WindowsPackage:
    full_name: str
    name: str | None
    publisher_id: str | None
    resource_id: str | None
    architecture: int
    version: str
    install_location: Path
    properties: int
    status_is_ok: bool

    @property
    def family_name(self) -> str:
        if not self.name or not self.publisher_id:
            return ""
        return f"{self.name}_{self.publisher_id}"


def _windows_dlls() -> tuple[object, object, object]:
    """Load the exact Win32 APIs used by the Desktop identity contract."""
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        wintrust = ctypes.WinDLL("wintrust", use_last_error=True)
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    except (AttributeError, OSError) as exc:
        raise CodexExecutableUnavailable("windows_native_api_unavailable") from exc

    kernel32.FindPackagesByPackageFamily.argtypes = (
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_wchar_p),
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.c_wchar_p,
        ctypes.POINTER(ctypes.c_uint32),
    )
    kernel32.FindPackagesByPackageFamily.restype = ctypes.c_long
    kernel32.PackageIdFromFullName.argtypes = (
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_ubyte),
    )
    kernel32.PackageIdFromFullName.restype = ctypes.c_long
    kernel32.GetPackagePathByFullName.argtypes = (
        ctypes.c_wchar_p,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.c_wchar_p,
    )
    kernel32.GetPackagePathByFullName.restype = ctypes.c_long
    wintrust.WinVerifyTrust.argtypes = (
        ctypes.c_void_p,
        ctypes.POINTER(_Guid),
        ctypes.POINTER(_WintrustData),
    )
    wintrust.WinVerifyTrust.restype = ctypes.c_long
    crypt32.CryptQueryObject.argtypes = (
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
    )
    crypt32.CryptQueryObject.restype = ctypes.c_int
    crypt32.CryptMsgGetParam.argtypes = (
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint32),
    )
    crypt32.CryptMsgGetParam.restype = ctypes.c_int
    crypt32.CertFindCertificateInStore.argtypes = (
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_void_p,
    )
    crypt32.CertFindCertificateInStore.restype = ctypes.c_void_p
    crypt32.CertNameToStrW.argtypes = (
        ctypes.c_uint32,
        ctypes.POINTER(_CryptDataBlob),
        ctypes.c_uint32,
        ctypes.c_wchar_p,
        ctypes.c_uint32,
    )
    crypt32.CertNameToStrW.restype = ctypes.c_uint32
    crypt32.CertFreeCertificateContext.argtypes = (ctypes.c_void_p,)
    crypt32.CertFreeCertificateContext.restype = ctypes.c_int
    crypt32.CertCloseStore.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
    crypt32.CertCloseStore.restype = ctypes.c_int
    crypt32.CryptMsgClose.argtypes = (ctypes.c_void_p,)
    crypt32.CryptMsgClose.restype = ctypes.c_int
    return kernel32, wintrust, crypt32


class _CtypesWindowsPackageStatusAbi:
    """Small native WinRT seam for Package.Status.VerifyIsOk on the current user.

    The package query APIs identify installed AppX packages, but their status is
    exposed by the supported Windows.ApplicationModel.Package WinRT surface.
    Keep the COM details here so the resolver has one fail-closed boolean seam.
    """

    _runtime_class = "Windows.Management.Deployment.PackageManager"

    def __init__(self) -> None:
        try:
            self._combase = ctypes.WinDLL("combase", use_last_error=True)
        except (AttributeError, OSError) as exc:
            raise CodexExecutableUnavailable("windows_package_status_unavailable") from exc
        self._combase.RoInitialize.argtypes = (ctypes.c_uint32,)
        self._combase.RoInitialize.restype = ctypes.c_int32
        self._combase.RoUninitialize.argtypes = ()
        self._combase.RoUninitialize.restype = None
        self._combase.WindowsCreateString.argtypes = (
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_void_p),
        )
        self._combase.WindowsCreateString.restype = ctypes.c_int32
        self._combase.WindowsDeleteString.argtypes = (ctypes.c_void_p,)
        self._combase.WindowsDeleteString.restype = ctypes.c_int32
        self._combase.RoActivateInstance.argtypes = (
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        )
        self._combase.RoActivateInstance.restype = ctypes.c_int32
        self._stdcall = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)

    @staticmethod
    def _failed(result: int) -> bool:
        return (int(result) & 0x80000000) != 0

    def _hstring(self, value: str) -> ctypes.c_void_p:
        result = ctypes.c_void_p()
        # WindowsCreateString counts UTF-16 code units rather than Python code
        # points. Package names are ASCII today; retain correct ABI semantics.
        length = len(value.encode("utf-16-le")) // 2
        hresult = self._combase.WindowsCreateString(value, length, ctypes.byref(result))
        if self._failed(hresult):
            self._delete_hstring(result)
            raise _windows_package_status_unavailable(hresult)
        return result

    def _delete_hstring(self, value: ctypes.c_void_p | None) -> None:
        if value is not None and value.value:
            self._combase.WindowsDeleteString(value)

    def _method(
        self,
        interface: ctypes.c_void_p,
        slot: int,
        result_type: object,
        *argument_types: object,
    ) -> object:
        if not interface.value:
            raise _windows_package_status_unavailable()
        vtable = ctypes.cast(
            interface, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
        ).contents
        address = vtable[slot]
        if not address:
            raise _windows_package_status_unavailable()
        return self._stdcall(result_type, ctypes.c_void_p, *argument_types)(address)

    def _query_interface(
        self, interface: ctypes.c_void_p, interface_id: _Guid
    ) -> ctypes.c_void_p:
        result = ctypes.c_void_p()
        method = self._method(
            interface,
            0,
            ctypes.c_int32,
            ctypes.POINTER(_Guid),
            ctypes.POINTER(ctypes.c_void_p),
        )
        hresult = method(interface, ctypes.byref(interface_id), ctypes.byref(result))
        if self._failed(hresult) or not result.value:
            if result.value:
                self.release(result)
            raise _windows_package_status_unavailable(hresult if self._failed(hresult) else None)
        return result

    def release(self, interface: ctypes.c_void_p | None) -> None:
        if interface is None or not interface.value:
            return
        method = self._method(interface, 2, ctypes.c_uint32)
        method(interface)

    def initialize(self) -> bool:
        result = self._combase.RoInitialize(_RO_INIT_MULTITHREADED)
        if not self._failed(result):
            return True
        if result == _RPC_E_CHANGED_MODE:
            # The current thread already belongs to a different apartment; it
            # can still make WinRT calls, but this invocation must not balance it.
            return False
        raise _windows_package_status_unavailable()

    def uninitialize(self, initialized_here: bool) -> None:
        if initialized_here:
            self._combase.RoUninitialize()

    def package_manager(self) -> ctypes.c_void_p:
        runtime_class: ctypes.c_void_p | None = None
        instance: ctypes.c_void_p | None = None
        manager: ctypes.c_void_p | None = None
        try:
            runtime_class = self._hstring(self._runtime_class)
            instance = ctypes.c_void_p()
            hresult = self._combase.RoActivateInstance(
                runtime_class, ctypes.byref(instance)
            )
            if self._failed(hresult) or not instance.value:
                raise _windows_package_status_unavailable(
                    hresult if self._failed(hresult) else None
                )
            manager = self._query_interface(instance, _IID_IPACKAGE_MANAGER)
            result = manager
            manager = None
            return result
        finally:
            self.release(manager)
            self.release(instance)
            self._delete_hstring(runtime_class)

    def find_current_user_package(
        self, manager: ctypes.c_void_p, full_name: str
    ) -> ctypes.c_void_p:
        user_security_id: ctypes.c_void_p | None = None
        package_full_name: ctypes.c_void_p | None = None
        package: ctypes.c_void_p | None = None
        try:
            user_security_id = self._hstring("")
            package_full_name = self._hstring(full_name)
            package = ctypes.c_void_p()
            method = self._method(
                manager,
                _IPACKAGE_MANAGER_FIND_PACKAGE_BY_USER_SECURITY_ID_PACKAGE_FULL_NAME,
                ctypes.c_int32,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_void_p),
            )
            hresult = method(
                manager,
                user_security_id,
                package_full_name,
                ctypes.byref(package),
            )
            if self._failed(hresult) or not package.value:
                raise _windows_package_status_unavailable(
                    hresult if self._failed(hresult) else None
                )
            result = package
            package = None
            return result
        finally:
            self.release(package)
            self._delete_hstring(package_full_name)
            self._delete_hstring(user_security_id)

    def verify_package_is_ok(self, package: ctypes.c_void_p) -> bool:
        package3 = self._query_interface(package, _IID_IPACKAGE3)
        status = ctypes.c_void_p()
        status_interface: ctypes.c_void_p | None = None
        try:
            status_method = self._method(
                package3, _IPACKAGE3_STATUS, ctypes.c_int32, ctypes.POINTER(ctypes.c_void_p)
            )
            hresult = status_method(package3, ctypes.byref(status))
            if self._failed(hresult) or not status.value:
                raise _windows_package_status_unavailable(
                    hresult if self._failed(hresult) else None
                )
            status_interface = self._query_interface(status, _IID_IPACKAGE_STATUS)
            verified = ctypes.c_ubyte()
            verify_method = self._method(
                status_interface,
                _IPACKAGE_STATUS_VERIFY_IS_OK,
                ctypes.c_int32,
                ctypes.POINTER(ctypes.c_ubyte),
            )
            hresult = verify_method(status_interface, ctypes.byref(verified))
            if self._failed(hresult):
                raise _windows_package_status_unavailable(hresult)
            return bool(verified.value)
        finally:
            self.release(status_interface)
            self.release(status)
            self.release(package3)


def _windows_package_status_abi() -> _CtypesWindowsPackageStatusAbi:
    return _CtypesWindowsPackageStatusAbi()


def _windows_package_status_is_ok(full_name: str) -> bool:
    """Read current-user Package.Status.VerifyIsOk and fail closed on any ABI error."""
    api = _windows_package_status_abi()
    initialized_here = False
    manager: ctypes.c_void_p | None = None
    package: ctypes.c_void_p | None = None
    try:
        initialized_here = api.initialize()
        manager = api.package_manager()
        package = api.find_current_user_package(manager, full_name)
        return api.verify_package_is_ok(package)
    except CodexExecutableUnavailable:
        raise
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        raise CodexExecutableUnavailable("windows_package_status_unavailable") from exc
    finally:
        api.release(package)
        api.release(manager)
        api.uninitialize(initialized_here)


def _wstring_from_pointer(value: int | None) -> str | None:
    return ctypes.wstring_at(value) if value else None


def _windows_package_id_from_full_name(full_name: str) -> tuple[str | None, str | None, str | None, int, str]:
    kernel32, _wintrust, _crypt32 = _windows_dlls()
    byte_length = ctypes.c_uint32()
    status = kernel32.PackageIdFromFullName(
        full_name, _PACKAGE_INFORMATION_FULL, ctypes.byref(byte_length), None
    )
    if status != _ERROR_INSUFFICIENT_BUFFER or not byte_length.value:
        raise CodexExecutableUnavailable("windows_package_probe_failed")
    buffer = (ctypes.c_ubyte * byte_length.value)()
    status = kernel32.PackageIdFromFullName(
        full_name,
        _PACKAGE_INFORMATION_FULL,
        ctypes.byref(byte_length),
        ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
    )
    if status != 0 or byte_length.value < ctypes.sizeof(_PackageId):
        raise CodexExecutableUnavailable("windows_package_probe_failed")
    package_id = ctypes.cast(buffer, ctypes.POINTER(_PackageId)).contents
    parts = package_id.version.parts
    return (
        _wstring_from_pointer(package_id.name),
        _wstring_from_pointer(package_id.publisher_id),
        _wstring_from_pointer(package_id.resource_id),
        int(package_id.processor_architecture),
        f"{parts.major}.{parts.minor}.{parts.build}.{parts.revision}",
    )


def _windows_package_path_by_full_name(full_name: str) -> Path:
    kernel32, _wintrust, _crypt32 = _windows_dlls()
    length = ctypes.c_uint32()
    status = kernel32.GetPackagePathByFullName(full_name, ctypes.byref(length), None)
    if status != _ERROR_INSUFFICIENT_BUFFER or not length.value:
        raise CodexExecutableUnavailable("windows_package_probe_failed")
    buffer = ctypes.create_unicode_buffer(length.value)
    status = kernel32.GetPackagePathByFullName(full_name, ctypes.byref(length), buffer)
    if status != 0 or not buffer.value:
        raise CodexExecutableUnavailable("windows_package_probe_failed")
    return Path(buffer.value)


def _windows_packages_by_family() -> tuple[_WindowsPackage, ...]:
    kernel32, _wintrust, _crypt32 = _windows_dlls()
    count = ctypes.c_uint32()
    buffer_length = ctypes.c_uint32()
    status = kernel32.FindPackagesByPackageFamily(
        _WINDOWS_PACKAGE_FAMILY,
        _PACKAGE_FILTER_HEAD | _PACKAGE_FILTER_DIRECT,
        ctypes.byref(count),
        None,
        ctypes.byref(buffer_length),
        None,
        None,
    )
    if status not in {0, _ERROR_INSUFFICIENT_BUFFER}:
        raise CodexExecutableUnavailable("windows_package_probe_failed")
    if not count.value:
        return ()
    if not buffer_length.value:
        raise CodexExecutableUnavailable("windows_package_probe_failed")
    full_names = (ctypes.c_wchar_p * count.value)()
    buffer = ctypes.create_unicode_buffer(buffer_length.value)
    properties = (ctypes.c_uint32 * count.value)()
    status = kernel32.FindPackagesByPackageFamily(
        _WINDOWS_PACKAGE_FAMILY,
        _PACKAGE_FILTER_HEAD | _PACKAGE_FILTER_DIRECT,
        ctypes.byref(count),
        full_names,
        ctypes.byref(buffer_length),
        buffer,
        properties,
    )
    if status != 0 or not count.value:
        raise CodexExecutableUnavailable("windows_package_probe_failed")
    packages: list[_WindowsPackage] = []
    for index in range(count.value):
        full_name = full_names[index]
        if not isinstance(full_name, str) or not full_name:
            raise CodexExecutableUnavailable("windows_package_probe_failed")
        name, publisher_id, resource_id, architecture, version = _windows_package_id_from_full_name(full_name)
        packages.append(
            _WindowsPackage(
                full_name=full_name,
                name=name,
                publisher_id=publisher_id,
                resource_id=resource_id,
                architecture=architecture,
                version=version,
                install_location=_windows_package_path_by_full_name(full_name),
                properties=int(properties[index]),
                status_is_ok=_windows_package_status_is_ok(full_name),
            )
        )
    return tuple(packages)


def _windows_package_install_location(packages: Sequence[_WindowsPackage]) -> _WindowsPackage:
    if not packages:
        raise CodexExecutableUnavailable("windows_package_missing")
    if len(packages) != 1:
        raise CodexExecutableUnavailable("windows_package_ambiguous")
    package = packages[0]
    if (
        package.name != _WINDOWS_PACKAGE_NAME
        or package.family_name.casefold() != _WINDOWS_PACKAGE_FAMILY.casefold()
        or package.architecture != _PROCESSOR_ARCHITECTURE_AMD64
        or not package.status_is_ok
        or package.resource_id not in {None, ""}
        or package.properties & _PACKAGE_PROPERTY_RESOURCE
        or not package.install_location.is_absolute()
    ):
        raise CodexExecutableUnavailable("windows_package_identity_untrusted")
    return package


def _windows_resource_path(package: _WindowsPackage) -> Path:
    install_location = package.install_location
    if not _directory_without_reparse(install_location):
        raise CodexExecutableUnavailable("windows_package_resource_unavailable", path=install_location)
    resource_parent = install_location
    for component in _WINDOWS_RESOURCE_RELATIVE.parts[:-1]:
        resource_parent = resource_parent / component
        if not _directory_without_reparse(resource_parent):
            raise CodexExecutableUnavailable("windows_package_resource_unavailable", path=resource_parent)
    resource = install_location / _WINDOWS_RESOURCE_RELATIVE
    if not _regular_file_without_reparse(resource):
        raise CodexExecutableUnavailable("windows_package_resource_unavailable", path=resource)
    return resource


def _windows_verify_trust(path: Path) -> int | None:
    _kernel32, wintrust, _crypt32 = _windows_dlls()
    file_info = _WintrustFileInfo(
        cb_struct=ctypes.sizeof(_WintrustFileInfo),
        path=str(path),
        file_handle=None,
        known_subject=None,
    )
    trust_data = _WintrustData(
        cb_struct=ctypes.sizeof(_WintrustData),
        policy_callback_data=None,
        sip_client_data=None,
        ui_choice=_WTD_UI_NONE,
        revocation_checks=_WTD_REVOKE_NONE,
        union_choice=_WTD_CHOICE_FILE,
        file_info=ctypes.pointer(file_info),
        state_action=_WTD_STATEACTION_IGNORE,
        state_data=None,
        url_reference=None,
        provider_flags=0,
        ui_context=0,
        signature_settings=None,
    )
    action = _Guid(
        0x00AAC56B,
        0xCD44,
        0x11D0,
        (ctypes.c_ubyte * 8)(0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE),
    )
    # WinVerifyTrust returns a signed 32-bit LONG. Keep the native status for
    # the private failure receipt; callers still decide trust by requiring 0.
    return _bounded_windows_long(
        wintrust.WinVerifyTrust(None, ctypes.byref(action), ctypes.byref(trust_data))
    )


def _windows_certificate_subject(path: Path) -> str | None:
    _kernel32, _wintrust, crypt32 = _windows_dlls()
    store = ctypes.c_void_p()
    message = ctypes.c_void_p()
    certificate = ctypes.c_void_p()
    try:
        if not crypt32.CryptQueryObject(
            _CERT_QUERY_OBJECT_FILE,
            ctypes.c_wchar_p(str(path)),
            _CERT_QUERY_CONTENT_FLAG_PKCS7_SIGNED_EMBED,
            _CERT_QUERY_FORMAT_FLAG_BINARY,
            0,
            None,
            None,
            None,
            ctypes.byref(store),
            ctypes.byref(message),
            None,
        ):
            return None
        size = ctypes.c_uint32()
        if not crypt32.CryptMsgGetParam(
            message, _CMSG_SIGNER_INFO_PARAM, 0, None, ctypes.byref(size)
        ) or size.value < ctypes.sizeof(_CmsgSignerInfo):
            return None
        signer_buffer = (ctypes.c_ubyte * size.value)()
        if not crypt32.CryptMsgGetParam(
            message,
            _CMSG_SIGNER_INFO_PARAM,
            0,
            ctypes.cast(signer_buffer, ctypes.c_void_p),
            ctypes.byref(size),
        ):
            return None
        signer = ctypes.cast(signer_buffer, ctypes.POINTER(_CmsgSignerInfo)).contents
        certificate_info = _CertInfo()
        certificate_info.issuer = signer.issuer
        certificate_info.serial_number = signer.serial_number
        certificate = ctypes.c_void_p(
            crypt32.CertFindCertificateInStore(
                store,
                _X509_ASN_ENCODING | _PKCS_7_ASN_ENCODING,
                0,
                _CERT_FIND_SUBJECT_CERT,
                ctypes.byref(certificate_info),
                None,
            )
        )
        if not certificate.value:
            return None
        context = ctypes.cast(certificate, ctypes.POINTER(_CertContext)).contents
        if not context.info:
            return None
        length = crypt32.CertNameToStrW(
            _X509_ASN_ENCODING | _PKCS_7_ASN_ENCODING,
            ctypes.byref(context.info.contents.subject),
            _CERT_X500_SUBJECT_STR,
            None,
            0,
        )
        if not length:
            return None
        output = ctypes.create_unicode_buffer(length)
        if not crypt32.CertNameToStrW(
            _X509_ASN_ENCODING | _PKCS_7_ASN_ENCODING,
            ctypes.byref(context.info.contents.subject),
            _CERT_X500_SUBJECT_STR,
            output,
            length,
        ):
            return None
        return output.value or None
    finally:
        if certificate.value:
            crypt32.CertFreeCertificateContext(certificate)
        if message.value:
            crypt32.CryptMsgClose(message)
        if store.value:
            crypt32.CertCloseStore(store, 0)


def _windows_authenticode_details(path: Path) -> dict[str, object]:
    wintrust_status = _windows_verify_trust(path)
    if wintrust_status is None or wintrust_status != 0:
        details: dict[str, object] = {"Status": "Invalid", "Publisher": None}
        if wintrust_status is not None:
            details["wintrust_status"] = wintrust_status
        return details
    return {
        "Status": "Valid",
        "Publisher": _windows_certificate_subject(path),
        "wintrust_status": 0,
    }


def _windows_signature_failure(
    path: Path, signature: dict[str, object]
) -> CodexExecutableUnavailable:
    """Build the stable public signature error with bounded private details."""
    error = CodexExecutableUnavailable("windows_cache_signature_untrusted", path=path)
    wintrust_status = _bounded_windows_long(signature.get("wintrust_status"))
    if wintrust_status is not None and wintrust_status != 0:
        setattr(error, "_wintrust_status", wintrust_status)
        return error

    # A zero WinVerifyTrust result means the remaining rejection is the exact
    # native X.500 subject allowlist. Preserve only bounded exact and normalized
    # forms in the private receipt; the public error remains unchanged.
    if signature.get("Status") == "Valid" and wintrust_status in {None, 0}:
        exact = signature.get("Publisher")
        normalized = _normalize_authenticode_subject(exact)
        if (
            isinstance(exact, str)
            and bool(exact)
            and len(exact) <= _AUTHENTICODE_SUBJECT_MAX_LENGTH
            and bool(normalized)
            and len(normalized) <= _AUTHENTICODE_SUBJECT_MAX_LENGTH
        ):
            setattr(error, "_authenticode_subject_exact", exact)
            setattr(error, "_authenticode_subject_normalized", normalized)
    return error


def _direct_cache_candidate(cache_root: Path, candidate: Path) -> bool:
    """Keep the managed-cache provenance one level below the fixed root."""
    try:
        root = cache_root.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
        relative = resolved.relative_to(root)
    except (OSError, ValueError):
        return False
    return len(relative.parts) == 2 and relative.parts[-1].casefold() == "codex.exe"


def _resolved_path_key(path: Path) -> str:
    try:
        return os.path.normcase(str(path.resolve(strict=True)))
    except OSError:
        return ""


def _verify_windows_appx_resource_hint(resource: Path) -> None:
    """Allow CODEX_CLI_PATH only as an exact, already-verified AppX hint."""
    configured = os.environ.get("CODEX_CLI_PATH")
    if not configured or not configured.strip():
        return
    requested = Path(configured)
    if _resolved_path_key(requested) != _resolved_path_key(resource):
        raise CodexExecutableUnavailable("windows_explicit_candidate_untrusted", path=requested)


def _select_windows_candidate(matching_candidates: Sequence[Path]) -> Path:
    """Require exactly one same-hash candidate from the fixed managed cache."""
    if not matching_candidates:
        raise CodexExecutableUnavailable("windows_cache_candidate_missing")
    if len(matching_candidates) != 1:
        raise CodexExecutableUnavailable("windows_cache_candidate_ambiguous")
    return matching_candidates[0]


def _windows_identity(*, cache_root: Path) -> CodexExecutableIdentity:
    package = _windows_package_install_location(_windows_packages_by_family())
    resource = _windows_resource_path(package)
    resource_sha256 = _sha256_file(resource)
    resource_size = resource.stat().st_size
    _verify_windows_appx_resource_hint(resource)

    if not _directory_without_reparse(cache_root):
        raise CodexExecutableUnavailable("windows_cache_root_unavailable", path=cache_root)
    matching_candidates: list[Path] = []
    found_cache_candidate = False
    try:
        children = sorted(cache_root.iterdir(), key=lambda item: item.name.casefold())
    except OSError as exc:
        raise CodexExecutableUnavailable("windows_cache_probe_failed", path=cache_root) from exc
    for child in children:
        if not _directory_without_reparse(child):
            continue
        candidate = child / "codex.exe"
        if not _regular_file_without_reparse(candidate):
            continue
        if not _direct_cache_candidate(cache_root, candidate):
            continue
        found_cache_candidate = True
        if _sha256_file(candidate) == resource_sha256:
            matching_candidates.append(candidate)

    if not matching_candidates:
        reason = (
            "windows_cache_resource_hash_mismatch"
            if found_cache_candidate
            else "windows_cache_candidate_missing"
        )
        raise CodexExecutableUnavailable(reason, path=cache_root)

    try:
        candidate = _select_windows_candidate(matching_candidates)
    except CodexExecutableUnavailable as exc:
        raise CodexExecutableUnavailable(
            exc.reason, path=exc.path or cache_root
        ) from exc
    signature = _windows_authenticode_details(candidate)
    if signature.get("Status") != "Valid":
        raise _windows_signature_failure(candidate, signature)
    publisher = signature.get("Publisher")
    if not _is_trusted_openai_authenticode_subject(publisher):
        raise _windows_signature_failure(candidate, signature)
    if (
        not _regular_file_without_reparse(candidate)
        or not _direct_cache_candidate(cache_root, candidate)
    ):
        raise CodexExecutableUnavailable("windows_cache_candidate_changed", path=candidate)
    candidate_sha256 = _sha256_file(candidate)
    candidate_size = candidate.stat().st_size
    if candidate_sha256 != resource_sha256:
        raise CodexExecutableUnavailable("windows_cache_candidate_changed", path=candidate)
    if (
        not _regular_file_without_reparse(resource)
        or _sha256_file(resource) != resource_sha256
        or resource.stat().st_size != resource_size
    ):
        raise CodexExecutableUnavailable("windows_package_resource_changed", path=resource)
    return CodexExecutableIdentity(
        path=str(candidate.resolve(strict=True)),
        source="windows_desktop_managed_cache",
        architectures=("x64",),
        package_family_name=_WINDOWS_PACKAGE_FAMILY,
        appx_package_full_name=package.full_name,
        sha256=candidate_sha256,
        signature_status="Valid",
        version=package.version or "unknown",
        size=candidate_size,
        publisher=publisher,
        provenance_root=str(cache_root.resolve(strict=True)),
        appx_resource_path=str(resource.resolve(strict=True)),
        appx_resource_version=package.version or "unknown",
        appx_resource_size=resource_size,
        appx_resource_sha256=resource_sha256,
    )


def verify_codex_executable_identity(
    identity: CodexExecutableIdentity,
) -> None:
    """Recheck the selected file immediately before a child is spawned."""
    if identity.source != "windows_desktop_managed_cache":
        return
    candidate = Path(identity.path)
    if (
        not candidate.is_absolute()
        or identity.package_family_name != _WINDOWS_PACKAGE_FAMILY
        or identity.signature_status != "Valid"
        or not isinstance(identity.publisher, str)
        or not _is_trusted_openai_authenticode_subject(identity.publisher)
        or not identity.sha256
        or identity.size is None
        or not identity.provenance_root
        or not identity.appx_package_full_name
        or not identity.appx_resource_path
        or not identity.appx_resource_sha256
        or identity.appx_resource_size is None
    ):
        raise CodexExecutableUnavailable("windows_cache_candidate_changed", path=candidate)
    root = Path(identity.provenance_root)
    if not _directory_without_reparse(root) or not _direct_cache_candidate(root, candidate):
        raise CodexExecutableUnavailable("windows_cache_candidate_changed", path=candidate)
    if not _regular_file_without_reparse(candidate):
        raise CodexExecutableUnavailable("windows_cache_candidate_changed", path=candidate)
    current_sha256 = _sha256_file(candidate)
    if current_sha256 != identity.sha256:
        raise CodexExecutableUnavailable("windows_cache_candidate_changed", path=candidate)
    if candidate.stat().st_size != identity.size:
        raise CodexExecutableUnavailable("windows_cache_candidate_changed", path=candidate)
    try:
        package = _windows_package_install_location(_windows_packages_by_family())
        resource = _windows_resource_path(package)
    except CodexExecutableUnavailable as exc:
        raise CodexExecutableUnavailable("windows_cache_candidate_changed", path=candidate) from exc
    if (
        package.full_name != identity.appx_package_full_name
        or str(resource.resolve(strict=True)) != identity.appx_resource_path
        or _sha256_file(resource) != identity.appx_resource_sha256
        or resource.stat().st_size != identity.appx_resource_size
        or identity.appx_resource_sha256 != identity.sha256
    ):
        raise CodexExecutableUnavailable("windows_cache_candidate_changed", path=candidate)
    signature = _windows_authenticode_details(candidate)
    if (
        signature.get("Status") != "Valid"
        or not _is_trusted_openai_authenticode_subject(signature.get("Publisher"))
    ):
        raise _windows_signature_failure(candidate, signature)
    # The signature API reads the path independently. Re-hash both bounded
    # copies afterwards so a replacement during certificate extraction cannot
    # reach the child.
    if (
        not _regular_file_without_reparse(candidate)
        or not _direct_cache_candidate(root, candidate)
        or _sha256_file(candidate) != identity.sha256
        or candidate.stat().st_size != identity.size
        or not _regular_file_without_reparse(resource)
        or _sha256_file(resource) != identity.appx_resource_sha256
        or resource.stat().st_size != identity.appx_resource_size
    ):
        raise CodexExecutableUnavailable("windows_cache_candidate_changed", path=candidate)


def _macos_identity(path: Path, *, source: str) -> CodexExecutableIdentity:
    if not _regular_executable(path):
        raise CodexExecutableUnavailable("candidate_not_executable", path=path)

    signature = _run_identity_tool(
        ("/usr/bin/codesign", "--display", "--verbose=4", str(path)), path=path
    )
    signature_text = f"{signature.stdout}\n{signature.stderr}"
    identifier_match = re.search(r"(?m)^Identifier=(\S+)$", signature_text)
    team_match = re.search(r"(?m)^TeamIdentifier=(\S+)$", signature_text)
    identifier = identifier_match.group(1) if identifier_match else None
    team_identifier = team_match.group(1) if team_match else None
    if identifier not in _OPENAI_CODEX_IDENTIFIERS or team_identifier != _OPENAI_TEAM_IDENTIFIER:
        raise CodexExecutableUnavailable("candidate_signature_untrusted", path=path)

    # The dedicated Lab intentionally has no Xcode Command Line Tools, so
    # /usr/bin/lipo opens an installation prompt instead of returning an
    # architecture.  `file` is part of the base OS and reports both thin and
    # universal Mach-O slices without adding that external dependency.
    architecture = _run_identity_tool(("/usr/bin/file", "-b", str(path)), path=path)
    architectures = tuple(
        dict.fromkeys(
            re.findall(r"(?<![A-Za-z0-9_])(arm64|x86_64)(?![A-Za-z0-9_])", architecture.stdout.lower())
        )
    )
    if "arm64" not in architectures:
        raise CodexExecutableUnavailable("candidate_architecture_unsupported", path=path)

    return CodexExecutableIdentity(
        path=str(path),
        source=source,
        architectures=architectures,
        signer_identifier=identifier,
        team_identifier=team_identifier,
    )


def resolve_codex_executable_identity(
    *,
    platform_name: str | None = None,
    machine_name: str | None = None,
    macos_primary: Path | None = None,
    macos_fallback: Path | None = None,
    windows_cache_root: Path | None = None,
) -> CodexExecutableIdentity:
    """Resolve one trusted executable without scanning PATH or the filesystem.

    Linux preserves its existing PATH-based command. Desktop platforms use only
    their bounded, identity-checked bundled executable locations.
    """

    selected_platform = platform_name or sys.platform
    if selected_platform not in {"darwin", "win32"}:
        return CodexExecutableIdentity(path="codex", source="legacy_system_path")

    selected_machine = (machine_name or platform.machine()).lower()
    if selected_platform == "win32":
        if selected_machine not in {"amd64", "x86_64"}:
            raise CodexExecutableUnavailable("platform_architecture_unsupported")
        cache_root = windows_cache_root
        if cache_root is None:
            local_app_data = os.environ.get("LOCALAPPDATA")
            if not local_app_data:
                raise CodexExecutableUnavailable("windows_local_app_data_missing")
            cache_root = Path(local_app_data) / _WINDOWS_CACHE_RELATIVE
        return _windows_identity(cache_root=cache_root)

    if selected_machine not in {"arm64", "aarch64"}:
        raise CodexExecutableUnavailable("platform_architecture_unsupported")

    primary = macos_primary or _MACOS_PRIMARY
    fallback = macos_fallback or _MACOS_BOUNDED_FALLBACK
    if primary.exists() or primary.is_symlink():
        return _macos_identity(primary, source="macos_codex_app_compatibility_path")
    if fallback.exists() or fallback.is_symlink():
        return _macos_identity(fallback, source="macos_chatgpt_app_bounded_fallback")
    raise CodexExecutableUnavailable("bounded_candidates_missing")


def resolve_codex_executable() -> str:
    return resolve_codex_executable_identity().path
