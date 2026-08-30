from src.hybrid.rank_fusion import RankedItem, ReciprocalRankFusion


def test_rrf_rewards_candidates_supported_by_multiple_independent_channels():
    fusion = ReciprocalRankFusion(k=60)

    result = fusion.fuse(
        {
            "lexical": [
                RankedItem("screen:other", 100.0),
                RankedItem("screen:ano", 2.0),
            ],
            "structural_dense": [
                RankedItem("screen:ano", 0.61),
                RankedItem("screen:other", 0.99),
            ],
            "semantic_dense": [
                RankedItem("screen:ano", 0.55),
            ],
        }
    )

    assert [candidate.canonical_id for candidate in result] == [
        "screen:ano",
        "screen:other",
    ]
    assert result[0].rrf_score > result[1].rrf_score
    assert result[0].channels == (
        "lexical",
        "structural_dense",
        "semantic_dense",
    )


def test_rrf_uses_rank_not_incomparable_raw_scores():
    fusion = ReciprocalRankFusion(k=60)

    result = fusion.fuse(
        {
            "lexical": [
                RankedItem("screen:first", 0.0001),
                RankedItem("screen:second", 9999.0),
            ]
        }
    )

    assert [candidate.canonical_id for candidate in result] == [
        "screen:first",
        "screen:second",
    ]
    assert result[0].contributions[0].raw_score == 0.0001
    assert result[1].contributions[0].raw_score == 9999.0


def test_rrf_deduplicates_within_channel_and_keeps_diagnostic_contributions():
    fusion = ReciprocalRankFusion(k=60)

    result = fusion.fuse(
        {
            "trigram": [
                RankedItem("screen:ano", 0.83),
                RankedItem("screen:ano", 0.81),
            ],
            "structural_dense": [
                RankedItem("screen:ano", 0.72),
            ],
        }
    )

    assert len(result) == 1
    assert [row.as_dict() for row in result[0].contributions] == [
        {
            "channel": "trigram",
            "rank": 1,
            "raw_score": 0.83,
        },
        {
            "channel": "structural_dense",
            "rank": 1,
            "raw_score": 0.72,
        },
    ]


def test_rrf_tie_break_prefers_deterministic_canonical_channel_without_weighting():
    fusion = ReciprocalRankFusion(k=60)

    result = fusion.fuse(
        {
            "canonical": [RankedItem("screen:ano", 1.0)],
            "structural_dense": [RankedItem("field:other", 0.99)],
        }
    )

    assert result[0].canonical_id == "screen:ano"
    assert result[0].rrf_score == result[1].rrf_score
