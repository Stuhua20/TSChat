"""Initialize application storage and manage login accounts."""

from __future__ import annotations

import argparse
import sys

import bcrypt
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from . import storage


def main():
    parser = argparse.ArgumentParser(description="管理应用账号")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    add = commands.add_parser("add")
    add.add_argument("username")
    add.add_argument("password")
    add.add_argument(
        "role", nargs="?", default="scientist", choices=["scientist", "admin"]
    )
    add.add_argument("display_name", nargs="?")
    commands.add_parser("list")
    password = commands.add_parser("passwd")
    password.add_argument("username")
    password.add_argument("password")
    delete = commands.add_parser("delete")
    delete.add_argument("username")
    args = parser.parse_args()
    storage.initialize()
    if args.command == "init":
        print("应用数据库已就绪。")
        return
    try:
        with storage.db() as conn:
            if args.command == "list":
                for row in conn.execute(
                    text(
                        "SELECT username,role,display_name,created_at FROM app_users ORDER BY created_at"
                    )
                ):
                    print("\t".join(str(value or "") for value in row))
            elif args.command == "add":
                hashed = bcrypt.hashpw(
                    args.password.encode(), bcrypt.gensalt()
                ).decode()
                conn.execute(
                    text(
                        "INSERT INTO app_users (username,password_hash,role,display_name) VALUES (:username,:password,:role,:display)"
                    ),
                    {
                        "username": args.username,
                        "password": hashed,
                        "role": args.role,
                        "display": args.display_name or args.username,
                    },
                )
                print("用户已创建。")
            elif args.command == "passwd":
                hashed = bcrypt.hashpw(
                    args.password.encode(), bcrypt.gensalt()
                ).decode()
                result = conn.execute(
                    text(
                        "UPDATE app_users SET password_hash=:password WHERE username=:username"
                    ),
                    {"username": args.username, "password": hashed},
                )
                conn.execute(
                    text("DELETE FROM web_sessions WHERE username=:username"),
                    {"username": args.username},
                )
                if not result.rowcount:
                    sys.exit("用户不存在。")
                print("密码已更新，现有登录已失效。")
            elif args.command == "delete":
                conn.execute(
                    text("DELETE FROM web_sessions WHERE username=:username"),
                    {"username": args.username},
                )
                result = conn.execute(
                    text("DELETE FROM app_users WHERE username=:username"),
                    {"username": args.username},
                )
                if not result.rowcount:
                    sys.exit("用户不存在。")
                print("用户已删除。")
    except IntegrityError:
        sys.exit("用户已存在。")


if __name__ == "__main__":
    main()
