import argparse
import json
from .meter import Meter
from .server import make_server


def main():
    parser = argparse.ArgumentParser(description="Local API usage accounting with fixed-window quotas")
    parser.add_argument("--db", default="meter.db")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-client")
    create.add_argument("id")
    create.add_argument("--quota", type=int, default=100)
    serve = commands.add_parser("serve")
    serve.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    try:
        meter = Meter(args.db)
        if args.command == "create-client":
            print(json.dumps({"client": args.id, "api_key": meter.register(args.id, args.quota)}))
        else:
            server = make_server(meter, args.port)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
    except (ValueError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
