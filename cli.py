from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from .adb import AdbClient, AdbError, Device
from .assignments import load_assignments
from .proxy import Proxy, ProxyParseError, parse_proxy
from .state import StateStore
from .xiaowei import XiaoweiClient, XiaoweiError


DEFAULT_STATE = Path(__file__).resolve().parent / "data" / "state.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xiaowei-proxy",
        description="Quản lý proxy cho box phone qua ADB; adapter Xiaowei sẽ cắm thêm sau.",
    )
    parser.add_argument("--adb", help="Đường dẫn adb (mặc định lấy ADB_PATH hoặc adb).")
    parser.add_argument(
        "--backend",
        choices=("adb", "xiaowei"),
        default="adb",
        help="Backend điều khiển thiết bị.",
    )
    parser.add_argument(
        "--xiaowei-url",
        default="ws://127.0.0.1:22222/",
        help="WebSocket API local của Xiaowei.",
    )
    parser.add_argument("--state", default=str(DEFAULT_STATE), help="Đường dẫn state JSON.")

    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("devices", help="Liệt kê thiết bị ADB.")

    status = commands.add_parser("status", help="Đọc http_proxy hiện tại trên thiết bị.")
    status.add_argument("--serial", action="append", help="Serial cần xem; có thể lặp lại.")
    status.add_argument("--all", action="store_true", help="Xem tất cả thiết bị.")

    apply_cmd = commands.add_parser("apply", help="Đặt proxy cho một hoặc nhiều thiết bị.")
    source = apply_cmd.add_mutually_exclusive_group(required=True)
    source.add_argument("--proxy", help="Proxy host:port:username:password.")
    source.add_argument("--file", help="CSV mapping với cột serial,proxy.")
    target = apply_cmd.add_mutually_exclusive_group()
    target.add_argument("--serial", action="append", help="Serial đích; có thể lặp lại.")
    target.add_argument("--all", action="store_true", help="Áp dụng cho mọi thiết bị trạng thái device.")
    apply_cmd.add_argument(
        "--allow-auth-unsupported",
        action="store_true",
        help="Cho phép ADB chỉ đặt host:port; username/password không được Android global proxy dùng.",
    )
    apply_cmd.add_argument("--dry-run", action="store_true", help="Chỉ hiển thị thay đổi, không gọi lệnh ghi.")

    clear = commands.add_parser("clear", help="Xóa global HTTP proxy.")
    clear.add_argument("--serial", action="append", help="Serial cần xóa; có thể lặp lại.")
    clear.add_argument("--all", action="store_true", help="Xóa trên mọi thiết bị trạng thái device.")
    clear.add_argument("--dry-run", action="store_true")

    rollback = commands.add_parser("rollback", help="Khôi phục giá trị trước lần apply gần nhất.")
    rollback.add_argument("--serial", action="append", help="Serial cần rollback; có thể lặp lại.")
    rollback.add_argument("--all", action="store_true", help="Rollback mọi thiết bị có history.")
    rollback.add_argument("--dry-run", action="store_true")
    return parser


def _usable_devices(adb: AdbClient) -> list[Device]:
    return [device for device in adb.list_devices() if device.usable]


def _select_devices(adb: AdbClient, serials: list[str] | None, all_devices: bool) -> list[Device]:
    devices = _usable_devices(adb)
    by_serial = {device.serial: device for device in devices}
    if serials:
        missing = [serial for serial in serials if serial not in by_serial]
        if missing:
            raise AdbError(f"Không tìm thấy thiết bị ADB ở trạng thái device: {', '.join(missing)}")
        return [by_serial[serial] for serial in serials]
    if all_devices:
        if not devices:
            raise AdbError("Không có thiết bị ADB nào ở trạng thái device.")
        return devices
    raise ValueError("Cần truyền --serial SERIAL hoặc --all.")


def _print_devices(devices: list[Device], adb: AdbClient, include_model: bool = False) -> None:
    if not devices:
        print("Không có thiết bị ADB.")
        return
    for device in devices:
        suffix = f" [{device.details}]" if device.details else ""
        if include_model and device.usable:
            model = adb.get_model(device.serial)
            suffix += f" model={model.get('model') or '?'} android={model.get('android') or '?'}"
        print(f"{device.serial}\t{device.state}{suffix}")


def _print_proxy(proxy: str | None) -> str:
    return proxy if proxy else "(none)"


def command_devices(adb: AdbClient) -> int:
    _print_devices(adb.list_devices(), adb, include_model=True)
    return 0


def command_status(adb: AdbClient, state: StateStore, args: argparse.Namespace) -> int:
    devices = _select_devices(adb, args.serial, args.all)
    for device in devices:
        actual = adb.get_global_proxy(device.serial)
        recorded = state.current(device.serial)
        print(f"{device.serial}\tactual={_print_proxy(actual)}\trecorded={_print_proxy(recorded)}")
    return 0


def _apply_one(
    adb: AdbClient,
    state: StateStore,
    device: Device,
    proxy: Proxy,
    *,
    allow_auth_unsupported: bool,
    dry_run: bool,
) -> bool:
    print(f"{device.serial}\t{proxy.redacted}\tendpoint={proxy.endpoint}")
    before = adb.get_global_proxy(device.serial)
    if dry_run:
        print(f"  DRY-RUN: {before or '(none)'} -> {proxy.endpoint}")
        if not allow_auth_unsupported:
            print(
                "  NOTE: apply thật sẽ cần --allow-auth-unsupported vì ADB "
                "chưa truyền username/password.",
                file=sys.stderr,
            )
        return True
    if not allow_auth_unsupported:
        print(
            "  SKIP: ADB global proxy chỉ nhận endpoint, chưa truyền được "
            "username/password. Dùng --allow-auth-unsupported để đặt endpoint-only.",
            file=sys.stderr,
        )
        return False
    actual = adb.set_global_proxy(device.serial, proxy.endpoint)
    if actual != proxy.endpoint:
        raise AdbError(
            f"{device.serial}: verify thất bại, mong đợi {proxy.endpoint!r}, nhận {actual!r}"
        )
    state.record_change(
        device.serial,
        before=before,
        after=actual,
        label=proxy.redacted,
    )
    print(f"  OK: {_print_proxy(before)} -> {actual}")
    return True


def command_apply(adb: AdbClient, state: StateStore, args: argparse.Namespace) -> int:
    if args.file:
        assignments = load_assignments(args.file)
        devices = _select_devices(adb, list(assignments), False)
        pairs = [(device, assignments[device.serial]) for device in devices]
    else:
        proxy = parse_proxy(args.proxy)
        devices = _select_devices(adb, args.serial, args.all)
        pairs = [(device, proxy) for device in devices]

    changed = 0
    for device, proxy in pairs:
        if _apply_one(
            adb,
            state,
            device,
            proxy,
            allow_auth_unsupported=args.allow_auth_unsupported,
            dry_run=args.dry_run,
        ):
            changed += 1
    if not args.dry_run and changed:
        state.save()
    return 0 if changed == len(pairs) else 2


def command_clear(adb: AdbClient, state: StateStore, args: argparse.Namespace) -> int:
    devices = _select_devices(adb, args.serial, args.all)
    for device in devices:
        before = adb.get_global_proxy(device.serial)
        if args.dry_run:
            print(f"{device.serial}\tDRY-RUN: {_print_proxy(before)} -> (none)")
            continue
        actual = adb.clear_global_proxy(device.serial)
        if actual is not None:
            raise AdbError(f"{device.serial}: không xóa được proxy, vẫn còn {actual!r}")
        state.record_change(device.serial, before=before, after=None, label="clear")
        print(f"{device.serial}\tOK: {_print_proxy(before)} -> (none)")
    if not args.dry_run:
        state.save()
    return 0


def command_rollback(adb: AdbClient, state: StateStore, args: argparse.Namespace) -> int:
    if args.serial:
        devices = _select_devices(adb, args.serial, False)
    elif args.all:
        known = [
            serial for serial, value in state.data.get("devices", {}).items()
            if value.get("history")
        ]
        devices = _select_devices(adb, known, False) if known else []
    else:
        raise ValueError("Cần truyền --serial SERIAL hoặc --all.")

    changed = 0
    for device in devices:
        target = state.rollback_target(device.serial)
        if target is None:
            print(f"{device.serial}\tSKIP: không có proxy cũ trong history.")
            continue
        before = adb.get_global_proxy(device.serial)
        if args.dry_run:
            print(f"{device.serial}\tDRY-RUN: {_print_proxy(before)} -> {_print_proxy(target)}")
            changed += 1
            continue
        if target == "":
            actual = adb.clear_global_proxy(device.serial)
        else:
            actual = adb.set_global_proxy(device.serial, target)
        if target == "":
            verified = actual is None
        else:
            verified = actual == target
        if not verified:
            raise AdbError(f"{device.serial}: rollback verify thất bại ({actual!r} != {target!r})")
        state.record_change(device.serial, before=before, after=actual, label="rollback")
        print(f"{device.serial}\tOK: {_print_proxy(before)} -> {actual}")
        changed += 1
    if not args.dry_run and changed:
        state.save()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        adb = XiaoweiClient(args.xiaowei_url) if args.backend == "xiaowei" else AdbClient(args.adb)
        state = StateStore(args.state)
        if args.command == "devices":
            return command_devices(adb)
        if args.command == "status":
            return command_status(adb, state, args)
        if args.command == "apply":
            return command_apply(adb, state, args)
        if args.command == "clear":
            return command_clear(adb, state, args)
        if args.command == "rollback":
            return command_rollback(adb, state, args)
        parser.error("Command không hợp lệ.")
    except (AdbError, XiaoweiError, ProxyParseError, ValueError, RuntimeError) as exc:
        print(f"LỖI: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
