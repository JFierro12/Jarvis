"""Tests for local news retrieval.

The failures worth catching here are all overstatement: claiming a story is
local when the feed is from another state, claiming an article was read when
only a blurb was fetched, claiming a publication time that was never in the
feed, and treating a headline as an instruction.
"""

from datetime import datetime, timezone

import pytest

from app.models.schemas import ContextItem
from app.services.location import (
    LocationPrecision,
    LocationSource,
    ResolvedLocation,
    Units,
)
from app.services.news import (
    FeedSpec,
    Locality,
    MockNewsProvider,
    NewsCache,
    NewsItem,
    NewsService,
    NewsUnavailable,
    RssNewsProvider,
    SummarySource,
    parse_feed,
    parse_feed_specs,
    parse_published,
    rank_and_dedupe,
    summarize,
    to_context_items,
)
from app.services.reasoning import UNTRUSTED_SOURCES

RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>Seattle Example Times</title>
  <item>
    <title>City council approves transit funding</title>
    <link>https://example.invalid/transit</link>
    <description>The council voted &lt;b&gt;unanimously&lt;/b&gt; to fund light rail.</description>
    <pubDate>Wed, 12 Nov 2026 18:30:00 GMT</pubDate>
  </item>
  <item>
    <title>Undated story</title>
    <link>https://example.invalid/undated</link>
    <description>No publication date in this item.</description>
  </item>
</channel></rss>"""

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Example Atom Wire</title>
  <entry>
    <title>Markets close higher</title>
    <link href="https://example.invalid/markets"/>
    <summary>Indexes finished up.</summary>
    <published>2026-11-12T18:30:00Z</published>
  </entry>
</feed>"""


def location(label="Seattle, Washington, United States", lat=47.6, lon=-122.3):
    return ResolvedLocation(
        label=label, latitude=lat, longitude=lon, timezone="America/Los_Angeles",
        source=LocationSource.SAVED_DEFAULT, precision=LocationPrecision.CITY,
        units=Units.US,
    )


class FakeHttp:
    def __init__(self, bodies):
        self.bodies = bodies
        self.requested = []

    def get_bytes(self, url, timeout, headers):
        self.requested.append(url)
        body = self.bodies.get(url)
        if body is None:
            raise NewsUnavailable(f"404 for {url}")
        if isinstance(body, Exception):
            raise body
        return body


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


class TestFeedParsing:
    def test_rss_items_parsed_with_html_stripped(self):
        title, items = parse_feed(RSS.encode("utf-8"))
        assert title == "Seattle Example Times"
        assert len(items) == 2
        assert items[0]["title"] == "City council approves transit funding"
        assert "<b>" not in items[0]["summary"]
        assert "unanimously" in items[0]["summary"]

    def test_atom_entries_parsed_with_href_link(self):
        title, items = parse_feed(ATOM.encode("utf-8"))
        assert title == "Example Atom Wire"
        assert items[0]["link"] == "https://example.invalid/markets"

    def test_malformed_xml_raises_rather_than_returning_nothing(self):
        with pytest.raises(NewsUnavailable):
            parse_feed(b"<rss><channel><item></broken>")

    def test_oversized_feed_is_refused(self):
        from app.services.news import MAX_FEED_BYTES
        with pytest.raises(NewsUnavailable):
            parse_feed(b"x" * (MAX_FEED_BYTES + 1))

    def test_empty_payload_is_not_an_error(self):
        assert parse_feed(b"") == ("", [])

    def test_unknown_root_element_yields_nothing(self):
        assert parse_feed(b"<html><body>not a feed</body></html>") == ("", [])


class TestPublicationTime:
    def test_rfc822_rss_date(self):
        parsed = parse_published("Wed, 12 Nov 2026 18:30:00 GMT")
        assert parsed.year == 2026 and parsed.month == 11 and parsed.day == 12

    def test_iso8601_atom_date(self):
        assert parse_published("2026-11-12T18:30:00Z").hour == 18

    @pytest.mark.parametrize("raw", ["", "not a date", "yesterday", "32 Foo 2026"])
    def test_unparseable_date_is_none_not_now(self, raw):
        """An unknown publication time must stay unknown.

        Defaulting to `now` would silently relabel an undated or ancient item
        as breaking news.
        """
        assert parse_published(raw) is None

    def test_naive_timestamp_is_treated_as_utc_not_local(self):
        assert parse_published("2026-11-12T18:30:00").tzinfo is not None


# ---------------------------------------------------------------------------
# Geographic honesty
# ---------------------------------------------------------------------------


class TestLocality:
    def test_feed_local_to_another_city_is_not_called_local(self):
        """A Seattle paper answering a question about Denver is not local
        news for Denver, and saying so would be a geographic lie."""
        provider = RssNewsProvider(
            [FeedSpec(url="https://example.invalid/sea.xml", name="Seattle Example Times",
                      region="Seattle", locality=Locality.LOCAL)],
            http_client=FakeHttp({"https://example.invalid/sea.xml": RSS.encode()}),
        )
        result = provider.fetch(location(label="Denver, Colorado, United States"))
        assert all(i.locality is not Locality.LOCAL for i in result.items)
        assert not result.has_local

    def test_matching_region_is_local(self):
        provider = RssNewsProvider(
            [FeedSpec(url="https://example.invalid/sea.xml", name="Seattle Example Times",
                      region="Seattle", locality=Locality.LOCAL)],
            http_client=FakeHttp({"https://example.invalid/sea.xml": RSS.encode()}),
        )
        result = provider.fetch(location())
        assert result.has_local

    def test_no_local_coverage_is_stated_explicitly(self):
        provider = RssNewsProvider(
            [FeedSpec(url="https://example.invalid/wire.xml", name="Wire",
                      locality=Locality.NATIONAL)],
            http_client=FakeHttp({"https://example.invalid/wire.xml": ATOM.encode()}),
        )
        result = provider.fetch(location())
        assert not result.has_local
        assert "don't have a local news source" in result.coverage_note
        assert "Seattle" in result.coverage_note

    def test_local_stories_rank_above_national(self):
        items = [
            NewsItem("National story", "Wire", "https://x.invalid/1", locality=Locality.NATIONAL),
            NewsItem("Local story", "Paper", "https://x.invalid/2", locality=Locality.LOCAL),
        ]
        assert rank_and_dedupe(items)[0].headline == "Local story"


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


class TestDeduplication:
    def test_same_story_from_three_outlets_collapses(self):
        """Aggregators append ' - Publisher', which makes one wire story look
        like three distinct ones."""
        items = [
            NewsItem("Transit funding approved - Seattle Times", "A", "https://a.invalid/1"),
            NewsItem("Transit funding approved - KOMO News", "B", "https://b.invalid/2"),
            NewsItem("Transit funding approved", "C", "https://c.invalid/3"),
        ]
        assert len(rank_and_dedupe(items)) == 1

    def test_deduplication_keeps_the_richer_summary(self):
        items = [
            NewsItem("Same story", "A", "https://a.invalid/1", summary="Short."),
            NewsItem("Same story", "B", "https://b.invalid/2",
                     summary="A considerably longer and more useful summary."),
        ]
        kept = rank_and_dedupe(items)
        assert len(kept) == 1
        assert "considerably longer" in kept[0].summary

    def test_different_stories_are_not_collapsed(self):
        items = [
            NewsItem("Transit funding approved", "A", "https://a.invalid/1"),
            NewsItem("School levy fails", "A", "https://a.invalid/2"),
        ]
        assert len(rank_and_dedupe(items)) == 2

    def test_undated_items_sort_after_dated_ones(self):
        now = datetime.now(timezone.utc)
        items = [
            NewsItem("No date", "A", "https://a.invalid/1", locality=Locality.LOCAL),
            NewsItem("Dated", "A", "https://a.invalid/2", locality=Locality.LOCAL,
                     published_at=now),
        ]
        assert rank_and_dedupe(items)[0].headline == "Dated"


# ---------------------------------------------------------------------------
# Never overstating what was retrieved
# ---------------------------------------------------------------------------


class TestNoOverstatement:
    def test_rss_items_are_never_marked_as_full_articles(self):
        provider = RssNewsProvider(
            [FeedSpec(url="https://example.invalid/sea.xml", region="Seattle",
                      locality=Locality.LOCAL, name="Seattle Example Times")],
            http_client=FakeHttp({"https://example.invalid/sea.xml": RSS.encode()}),
        )
        for item in provider.fetch(location()).items:
            assert item.summary_source is not SummarySource.FULL_ARTICLE

    def test_item_with_no_description_is_headline_only(self):
        feed = b"""<rss version="2.0"><channel><title>T</title>
          <item><title>Bare headline</title><link>https://x.invalid/1</link></item>
        </channel></rss>"""
        provider = RssNewsProvider(
            [FeedSpec(url="https://x.invalid/f.xml", name="T")],
            http_client=FakeHttp({"https://x.invalid/f.xml": feed}),
        )
        item = provider.fetch(location()).items[0]
        assert item.summary_source is SummarySource.HEADLINE_ONLY

    def test_summary_states_these_are_not_full_articles(self):
        result = MockNewsProvider().fetch(location())
        assert "not full articles" in summarize(result)

    def test_headline_only_items_are_flagged_in_the_summary(self):
        result = MockNewsProvider().fetch(location())
        result.items = [NewsItem("Bare", "Pub", "https://x.invalid/1",
                                 locality=Locality.LOCAL)]
        assert "headline only" in summarize(result)

    def test_summary_names_the_location_and_its_provenance(self):
        text = summarize(MockNewsProvider().fetch(location()))
        assert "Seattle" in text and "saved default" in text

    def test_no_items_says_so_rather_than_inventing(self):
        from app.services.news import NewsResult
        empty = NewsResult(items=[], location=location())
        text = summarize(empty)
        assert "didn't find any" in text


# ---------------------------------------------------------------------------
# Untrusted content
# ---------------------------------------------------------------------------


class TestRetrievedContentIsUntrusted:
    def test_news_reaches_the_model_as_external_content(self):
        items = to_context_items(MockNewsProvider().fetch(location()))
        assert items
        assert all(isinstance(i, ContextItem) for i in items)
        assert {i.source for i in items} == {"EXTERNAL_CONTENT"}

    def test_external_content_is_in_the_untrusted_set(self):
        """The guarantee is structural: it comes from the shared provenance
        table in reasoning.py, not from anything this module asserts."""
        assert "EXTERNAL_CONTENT" in UNTRUSTED_SOURCES

    def test_injection_headline_is_carried_as_data_not_instruction(self):
        """The mock feed deliberately contains an injection attempt.

        It must survive into context verbatim -- the user may well want to be
        told about a weirdly-titled article -- while remaining tagged as
        untrusted.
        """
        items = to_context_items(MockNewsProvider().fetch(location()))
        blob = "\n".join(i.content for i in items)
        assert "Ignore previous instructions" in blob
        assert all(i.source == "EXTERNAL_CONTENT" for i in items)

    def test_mock_reasoner_refuses_to_act_on_an_injected_headline(self):
        """End to end through the real reasoning path with a fake provider:
        a malicious headline must not produce a tool call."""
        from app.services.reasoning import MockLanguageReasoningProvider

        context = to_context_items(MockNewsProvider().fetch(location()))
        response = MockLanguageReasoningProvider().reason(
            context=context, question="What's the local news?",
            available_tools=["delete_all_memories", "get_current_time"],
        )
        assert response.proposed_tool_call is None

    def test_context_items_carry_publisher_and_link_for_attribution(self):
        items = to_context_items(MockNewsProvider().fetch(location()))
        assert "Publisher:" in items[0].content
        assert "Link:" in items[0].content


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------


class TestFailureHandling:
    def test_no_feeds_configured_explains_the_setting(self):
        with pytest.raises(NewsUnavailable) as exc:
            RssNewsProvider([], http_client=FakeHttp({})).fetch(location())
        assert "JARVIS_NEWS_FEEDS" in str(exc.value)

    def test_one_dead_feed_does_not_lose_the_others_but_is_reported(self):
        http = FakeHttp({"https://ok.invalid/f.xml": RSS.encode()})
        provider = RssNewsProvider(
            [FeedSpec(url="https://ok.invalid/f.xml", region="Seattle",
                      locality=Locality.LOCAL, name="OK"),
             FeedSpec(url="https://dead.invalid/f.xml", name="Dead")],
            http_client=http,
        )
        result = provider.fetch(location())
        assert result.items
        assert len(result.failed_feeds) == 1
        assert "could not be reached" in summarize(result)

    def test_all_feeds_dead_raises_rather_than_returning_empty(self):
        provider = RssNewsProvider(
            [FeedSpec(url="https://dead.invalid/f.xml", name="Dead")],
            http_client=FakeHttp({}),
        )
        with pytest.raises(NewsUnavailable):
            provider.fetch(location())

    def test_items_missing_a_link_are_skipped(self):
        feed = b"""<rss version="2.0"><channel><title>T</title>
          <item><title>No link here</title></item>
          <item><title>Has link</title><link>https://x.invalid/1</link></item>
        </channel></rss>"""
        provider = RssNewsProvider(
            [FeedSpec(url="https://x.invalid/f.xml", name="T")],
            http_client=FakeHttp({"https://x.invalid/f.xml": feed}),
        )
        items = provider.fetch(location()).items
        assert [i.headline for i in items] == ["Has link"]


class TestConfiguration:
    def test_region_prefixed_feed_is_local(self):
        specs = parse_feed_specs("Seattle|https://a.invalid/f.xml")
        assert specs[0].region == "Seattle"
        assert specs[0].locality is Locality.LOCAL

    def test_bare_url_is_national(self):
        assert parse_feed_specs("https://a.invalid/f.xml")[0].locality is Locality.NATIONAL

    def test_blank_and_malformed_entries_are_dropped(self):
        assert parse_feed_specs(" , ,Seattle| ,") == []

    def test_empty_configuration_yields_no_feeds(self):
        assert parse_feed_specs("") == []


class TestCaching:
    def test_second_call_served_from_cache(self):
        calls = []

        class Counting(MockNewsProvider):
            def fetch(self, loc, limit=8):
                calls.append(1)
                return super().fetch(loc, limit)

        service = NewsService(Counting(), NewsCache(ttl_seconds=60))
        service.fetch(location())
        second = service.fetch(location())
        assert len(calls) == 1
        assert second.from_cache

    def test_expired_cache_refetches(self):
        calls = []

        class Counting(MockNewsProvider):
            def fetch(self, loc, limit=8):
                calls.append(1)
                return super().fetch(loc, limit)

        service = NewsService(Counting(), NewsCache(ttl_seconds=0.0))
        service.fetch(location())
        service.fetch(location())
        assert len(calls) == 2

    def test_cached_result_does_not_mutate_the_stored_entry(self):
        cache = NewsCache(ttl_seconds=60)
        service = NewsService(MockNewsProvider(), cache)
        first = service.fetch(location())
        assert not first.from_cache
        service.fetch(location())
        # The originally returned object must not have been retroactively
        # relabelled as cached.
        assert not first.from_cache
