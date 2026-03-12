"""
Jachin CLI 入口
"""
import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="jachin",
        description="Jachin Nexus Layer 2 命令行工具",
    )
    parser.add_argument(
        "-v", "--version",
        action="version",
        version="%(prog)s 1.0.0",
    )
    subparsers = parser.add_subparsers(dest="cmd", help="子命令")

    # jachin pair
    pair_parser = subparsers.add_parser("pair", help="OOBE 配对：获取 6 位码，完成与 Layer 1 的绑定")
    pair_parser.add_argument(
        "--base-url",
        default="http://localhost:3000",
        help="Layer 1 Nexus 基地址",
    )
    pair_parser.add_argument(
        "--no-daemon",
        action="store_true",
        help="配对成功后不自动启动 daemon",
    )

    # jachin daemon
    daemon_parser = subparsers.add_parser("daemon", help="启动 nexus_daemon 点火总控")
    daemon_parser.add_argument(
        "--foreground",
        action="store_true",
        help="前台运行（默认）",
    )

    # jachin status
    subparsers.add_parser("status", help="查看配对状态与配置")

    args = parser.parse_args()

    if args.cmd == "pair":
        from jachin_cli.commands.pair import run_pair
        return run_pair(args)
    if args.cmd == "daemon":
        from jachin_cli.commands.daemon import run_daemon
        return run_daemon(args)
    if args.cmd == "status":
        from jachin_cli.commands.status import run_status
        return run_status(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
