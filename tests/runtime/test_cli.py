from trustedsql.cli import build_parser


def test_cli_default_phase_is_runtime_only() -> None:
    args = build_parser().parse_args(["run"])
    assert args.command == "run"


