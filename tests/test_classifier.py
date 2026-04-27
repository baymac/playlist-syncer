"""Tests for the Beatport genre classifier and fuzzy matching."""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from beatport_sync import classifier, matching

bp = types.SimpleNamespace(
    classify=classifier.classify,
    EXACT=classifier.EXACT,
    CONTAINS=classifier.CONTAINS,
    DESTINATION_PLAYLISTS=classifier.DESTINATION_PLAYLISTS,
    combined_score=matching.combined_score,
    best_match=matching.best_match,
    search_query=matching.search_query,
    MATCH_THRESHOLD=matching.MATCH_THRESHOLD,
)


class TestClassify:
    def test_exact_match(self):
        assert bp.classify("house") == "House"
        assert bp.classify("tech house") == "Tech House"
        assert bp.classify("melodic house & techno") == "Melodic House"

    def test_exact_match_case_insensitive(self):
        assert bp.classify("House") == "House"
        assert bp.classify("TECH HOUSE") == "Tech House"

    def test_exact_match_strips_whitespace(self):
        assert bp.classify("  house  ") == "House"

    def test_contains_match(self):
        assert bp.classify("Psy Trance") == "Trance"
        assert bp.classify("Progressive Trance") == "Trance"
        assert bp.classify("Hard Dubstep") == "Dubstep"
        assert bp.classify("Minimal Techno") == "Minimal"

    def test_no_match_returns_none(self):
        assert bp.classify("pop") is None
        assert bp.classify("hip-hop") is None
        assert bp.classify("classical") is None
        assert bp.classify("jazz") is None

    def test_none_input(self):
        assert bp.classify(None) is None

    def test_empty_string(self):
        assert bp.classify("") is None

    def test_all_exact_keys_map_to_known_destinations(self):
        for genre, dest in bp.EXACT.items():
            assert dest in bp.DESTINATION_PLAYLISTS, f"{dest!r} not in DESTINATION_PLAYLISTS"

    def test_all_contains_dests_in_destination_playlists(self):
        for _, dest in bp.CONTAINS:
            assert dest in bp.DESTINATION_PLAYLISTS


class TestCombinedScore:
    def test_exact_match(self):
        score = bp.combined_score("Glue", "Bicep", "Glue", "Bicep")
        assert score > 0.95

    def test_artist_mismatch(self):
        score = bp.combined_score("Glue", "Bicep", "Glue", "Totally Different Artist")
        assert 0.4 < score < 0.8

    def test_completely_different(self):
        score = bp.combined_score("Glue", "Bicep", "Sandstorm", "Darude")
        assert score < 0.3

    def test_feat_stripped(self):
        score = bp.combined_score(
            "Glue (feat. Someone)", "Bicep",
            "Glue", "Bicep"
        )
        assert score > 0.85

    def test_bare_feat_stripped(self):
        # Beatport omits brackets: "On & On feat. Alika" vs Apple Music "(feat. Alika)"
        score = bp.combined_score(
            "On & On (feat. Alika)", "Armin van Buuren & Punctual",
            "On & On feat. Alika", "Armin van Buuren, Alika, Punctual",
        )
        assert score > bp.MATCH_THRESHOLD

    def test_remix_vs_plain_rejected(self):
        # AM has a remix; Beatport has the plain original — must NOT match (different version)
        score = bp.combined_score(
            "Sweet Disposition (John Summit & Silver Panda Remix)", "The Temper Trap",
            "Sweet Disposition", "The Temper Trap",
        )
        assert score < bp.MATCH_THRESHOLD

    def test_year_remix_matches_plain(self):
        # "(2022 Remix)" is a version label — Beatport often omits it from the title
        score = bp.combined_score(
            "Age of Love (2022 Remix)", "Dimitri Vegas & Like Mike, Age of Love & Vini Vici",
            "The Age Of Love", "Age Of Love",
        )
        assert score > bp.MATCH_THRESHOLD

    def test_bare_remix_tag_matches_plain(self):
        # "[Remix]" with no remixer name is a generic version label, not a specific remix
        score = bp.combined_score(
            "SAD GIRLZ LUV MONEY (feat. Moliy) [Remix]", "Amaarae & Kali Uchis",
            "Sad Girlz Luv Money", "Amaarae, Kali Uchis, Moliy",
        )
        assert score > bp.MATCH_THRESHOLD

    def test_single_word_descriptor_remix_matches_plain(self):
        # "(Albanian Remix)" is a style descriptor — Beatport lists the track without it
        score = bp.combined_score(
            "Habibi (Albanian Remix)", "Ricky Rich & Dardan",
            "Habibi", "Ricky Rich, Dardan",
        )
        assert score > bp.MATCH_THRESHOLD

    def test_multi_word_named_remix_still_rejected(self):
        # Two-word remixer name must still block (regression guard)
        score = bp.combined_score(
            "Sweet Disposition (John Summit & Silver Panda Remix)", "The Temper Trap",
            "Sweet Disposition", "The Temper Trap",
        )
        assert score < bp.MATCH_THRESHOLD

    def test_remixer_as_bp_artist_matches(self):
        # Beatport lists remixer as co-artist rather than in the title — should match
        score = bp.combined_score(
            "Father Ocean (Ben Böhmer Remix) [Edit]", "Monolink",
            "Father Ocean", "Monolink, Ben Böhmer",
        )
        assert score > bp.MATCH_THRESHOLD

    def test_remixer_not_in_bp_artist_still_rejected(self):
        # Named remixer in AM title, but BP only has original artist — block it
        score = bp.combined_score(
            "Sweet Disposition (John Summit Remix)", "The Temper Trap",
            "Sweet Disposition", "The Temper Trap",
        )
        assert score < bp.MATCH_THRESHOLD

    def test_multiword_remixer_as_bp_artist_matches(self):
        # [Jax Jones Remix] not in BP title but Jax Jones is a BP co-artist — should match
        score = bp.combined_score(
            "One By One (feat. Oaks) [Jax Jones Remix]", "Robin Schulz & Topic",
            "One By One feat. Oaks", "Robin Schulz, Topic, Jax Jones, Oaks",
        )
        assert score > bp.MATCH_THRESHOLD

    def test_x_connected_remixers_as_bp_artists(self):
        # "Anyma x Layton Giordani Remix" — x-connector split, both names in BP artist list
        score = bp.combined_score(
            "Last Night (Anyma x Layton Giordani Remix)", "Loofy",
            "Last Night", "Loofy, Anyma, Layton Giordani",
        )
        assert score > bp.MATCH_THRESHOLD

    def test_ampersand_connected_remixers_as_bp_artists(self):
        # Remixers come from the API's remixers field, merged into bp_artists by best_match
        candidates = [{"id": 1, "name": "Xplode",
                       "artists": [{"name": "Avancada"}, {"name": "Darius & Finlay"}],
                       "remixers": [{"name": "Grahham Bell"}, {"name": "Yoel Lewis"}],
                       "genre": {"name": "Trance (Main Floor)"}}]
        match, score = bp.best_match(
            "Xplode (Grahham Bell & Yoel Lewis Remix)", "Avancada & Darius & Finlay",
            candidates,
        )
        assert match is not None
        assert score > bp.MATCH_THRESHOLD

    def test_same_remix_matches(self):
        # Both sides carry the same remix tag — should match
        score = bp.combined_score(
            "Sweet Disposition (John Summit Remix)", "The Temper Trap",
            "Sweet Disposition (John Summit Remix)", "The Temper Trap",
        )
        assert score > 0.95

    def test_different_remixes_rejected(self):
        # Two different remixes of the same track must NOT match each other
        score = bp.combined_score(
            "I Remember (Vocal Mix)", "deadmau5 & Kaskade",
            "I Remember (John Summit Remix)", "Kaskade, deadmau5",
        )
        assert score < bp.MATCH_THRESHOLD

    def test_original_mix_stripped(self):
        score = bp.combined_score(
            "Sweet Disposition", "The Temper Trap",
            "Sweet Disposition (Original Mix)", "The Temper Trap",
        )
        assert score > 0.95

    def test_multi_artist_tokenised(self):
        # &/, delimiters must be split before normalising, not after
        score = bp.combined_score(
            "Track", "Artist A & Artist B",
            "Track", "Artist A, Artist B",
        )
        assert score > 0.95

    def test_edition_tag_and_case_insensitive_artist(self):
        # Edition tag in parens stripped; artist name casing differs (Kah-Lo vs Kah-lo)
        score = bp.combined_score(
            "Fake Id (Coke & Rum Edition)", "Riton, Kah-Lo & GEE LEE",
            "Fake ID",                      "Riton, Kah-lo, GEE LEE",
        )
        assert score > bp.MATCH_THRESHOLD

    def test_feat_in_parens_vs_bare_feat_multiartist(self):
        # AM: (feat. Hayla) in title, artists with &
        # BP: bare feat. in title, artists reordered with commas
        score = bp.combined_score(
            "Escape (feat. Hayla)", "Kx5, deadmau5 & Kaskade",
            "Escape feat. Hayla",   "Kaskade, deadmau5, Kx5",
        )
        assert score > bp.MATCH_THRESHOLD

    def test_subtitle_in_parens_stripped(self):
        # Apple Music appends a subtitle in parens that Beatport omits
        score = bp.combined_score(
            "Black Friday (pretty like the sun)", "Lost Frequencies & Tom Odell",
            "Black Friday", "Tom Odell, Lost Frequencies",
        )
        assert score > bp.MATCH_THRESHOLD

    def test_artist_order_and_bare_feat_in_title(self):
        # Beatport reorders artists and appends feat. to title
        score = bp.combined_score(
            "Go Back", "John Summit, Sub Focus & Julia Church",
            "Go Back feat. Julia Church", "Sub Focus, Julia Church, John Summit",
        )
        assert score > bp.MATCH_THRESHOLD

    def test_edit_tag_in_parens_stripped(self):
        # Apple Music includes "(James Hype Edit)"; Beatport has plain title, reversed artist order
        score = bp.combined_score(
            "Left To Right (James Hype Edit)", "Thomas Newson & Klubbheads",
            "Left To Right",                   "Klubbheads, Thomas Newson",
        )
        assert score > bp.MATCH_THRESHOLD

    def test_feat_artist_in_bp_artist_list(self):
        # AM feat. artist is listed as a BP co-artist; [Rivo Remix] is single-word = generic
        # AM artist "Disclosure" is a subset of BP's artist+remixers list → should match
        score = bp.combined_score(
            "You & Me (feat. Eliza Doolittle) [Rivo Remix]", "Disclosure",
            "You & Me", "Disclosure, Eliza Doolittle, Rivo",
        )
        assert score > bp.MATCH_THRESHOLD


class TestBestMatch:
    def _make_candidate(self, name: str, artist: str, genre: str = "House", track_id: int = 1) -> dict:
        return {
            "id": track_id,
            "name": name,
            "artists": [{"name": artist}],
            "genre": {"name": genre},
        }

    def test_finds_exact_match(self):
        candidates = [
            self._make_candidate("Glue", "Bicep", track_id=1),
            self._make_candidate("Sandstorm", "Darude", track_id=2),
        ]
        match, score = bp.best_match("Glue", "Bicep", candidates)
        assert match is not None
        assert match["id"] == 1
        assert score > bp.MATCH_THRESHOLD

    def test_returns_none_below_threshold(self):
        candidates = [self._make_candidate("Sandstorm", "Darude")]
        match, score = bp.best_match("Glue", "Bicep", candidates, threshold=0.72)
        assert match is None
        assert score < 0.72

    def test_empty_candidates(self):
        match, score = bp.best_match("Glue", "Bicep", [])
        assert match is None
        assert score == 0.0

    def test_custom_threshold(self):
        # "Glue" vs "Glueing" — similar but not identical, won't reach 0.99
        candidates = [self._make_candidate("Glueing", "Bicep")]
        match, score = bp.best_match("Glue", "Bicep", candidates, threshold=0.99)
        assert match is None
