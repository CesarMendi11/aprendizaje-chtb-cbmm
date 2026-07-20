from scripts.sync_approved_to_neo4j import build_parser


def test_sync_cli_options_are_explicit():
    args = build_parser().parse_args(
        [
            "--erp-id",
            "erp:synthetic",
            "--knowledge-version",
            "v1",
            "--batch-size",
            "25",
            "--replace-version",
            "--allow-empty",
            "--yes",
            "--pretty",
        ]
    )
    assert args.erp_id == "erp:synthetic" and args.batch_size == 25
    assert args.replace_version and args.allow_empty and args.yes and args.pretty


def test_dry_run_option_does_not_require_replace_confirmation():
    args = build_parser().parse_args(["--dry-run"])
    assert args.dry_run is True and args.yes is False
