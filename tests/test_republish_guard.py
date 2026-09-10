"""AC-2..AC-6 (nops-cycle-3) — the guard on the destructive republish path.

``republish_block_tree`` converges a page by deleting the blocks that changed.
The block ids are what comments are anchored to, and the Notion API cannot move
or re-anchor a comment, so a republish over a discussed page destroys the mapping
from every comment to the line it was about (ISS-029, confirmed twice).

The guard makes that refuse instead of proceed. Its shape is three claims, and
each is bound below:

1. **It refuses what would destroy** — a republish that would write to a page
   carrying an open discussion raises, having written nothing (AC-2).
2. **It costs nothing where nothing is at risk** — a no-op republish (identical
   content, zero writes, STATE D-91) is neither refused nor charged a comments
   request (AC-5), and neither is an explicit ``allow_destructive=True`` (AC-3).
3. **It knows only Notion-native facts** — "has a discussion" is answered by the
   Comments API for any page in any workspace; "is this page one of *mine*" is a
   caller-supplied predicate, and no workspace's schema or ids appear in the
   library (AC-4).

AC-6 (non-vacuity) is bound by ``TestGuardNonVacuity`` below, which neutralizes
the guard and shows the refusals disappear — the in-repo half of the mutation
pass described in ``mutations.md`` (M-1 deletes the guard call, M-2 flips
``allow_destructive``'s default).
"""

from __future__ import annotations

import ast
import copy
from pathlib import Path
from typing import Any

import pytest

from notion_ops.exceptions import DestructiveRepublishError
from notion_ops.utils.ids import extract_notion_id
from notion_ops.utils.publish import republish_block_tree, republish_markdown

from tests.test_republish_diff import PAGE, ContentFakeClient, _para

PAGE_ID = extract_notion_id(PAGE)


def _comment(cid: str, text: str, *, block_id: str | None = None) -> dict[str, Any]:
    parent = (
        {"type": "block_id", "block_id": block_id}
        if block_id
        else {"type": "page_id", "page_id": PAGE}
    )
    return {
        "object": "comment",
        "id": cid,
        "parent": parent,
        "discussion_id": f"d-{cid}",
        "created_by": {"object": "user", "id": "user-1"},
        "rich_text": [{"type": "text", "text": {"content": text}, "plain_text": text}],
    }


class CommentedFakeClient(ContentFakeClient):
    """``ContentFakeClient`` plus a comments endpoint that records its calls.

    ``comment_calls`` is the observable AC-3 and AC-5 assert on: the guard must
    not consult the comments endpoint on a path where nothing is at risk.
    """

    def __init__(
        self,
        initial: list[dict[str, Any]] | None = None,
        comments: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(initial=initial, **kwargs)
        self.comment_calls: list[dict[str, Any]] = []
        stored = list(comments or [])
        client = self

        class _Comments:
            def list(self, **params: Any) -> dict[str, Any]:
                client.comment_calls.append(dict(params))
                return {
                    "object": "list",
                    "results": stored,
                    "has_more": False,
                    "next_cursor": None,
                }

        self.api.comments = _Comments()


# ---------------------------------------------------------------------------
# AC-2 — a republish that would write to a discussed page raises, writing nothing
# ---------------------------------------------------------------------------


class TestRefusesDiscussedPage:
    def test_changed_content_on_a_commented_page_raises_and_writes_nothing(self):
        client = CommentedFakeClient(
            initial=[_para("alpha"), _para("beta")],
            comments=[_comment("c-1", "this paragraph is wrong", block_id="id-2")],
        )
        before = client.top_ids

        with pytest.raises(DestructiveRepublishError) as excinfo:
            republish_block_tree(client, PAGE, [_para("alpha"), _para("CHANGED")])

        # Nothing was written: no append, no delete, ids untouched.
        assert client.ops == []
        assert client.appended == []
        assert client.deleted == []
        assert client.top_ids == before

        message = str(excinfo.value)
        assert PAGE_ID in message
        assert "discussion" in message
        assert "1" in message
        assert "allow_destructive" in message
        assert excinfo.value.trigger == "discussion"
        assert excinfo.value.discussion_count == 1
        assert excinfo.value.page_id == PAGE_ID

    def test_refusal_message_carries_no_comment_text(self):
        """A refusal names the page and the count -- never what anyone said."""
        secret = "the number is 8675309"
        client = CommentedFakeClient(
            initial=[_para("alpha")],
            comments=[_comment("c-1", secret)],
        )

        with pytest.raises(DestructiveRepublishError) as excinfo:
            republish_block_tree(client, PAGE, [_para("CHANGED")])

        assert secret not in str(excinfo.value)
        assert "8675309" not in str(excinfo.value)

    def test_pure_deletion_is_also_a_write_and_is_also_refused(self):
        """Shrinking a page deletes blocks without appending any; still guarded."""
        client = CommentedFakeClient(
            initial=[_para("alpha"), _para("beta")],
            comments=[_comment("c-1", "keep beta")],
        )

        with pytest.raises(DestructiveRepublishError):
            republish_block_tree(client, PAGE, [_para("alpha")])

        assert client.ops == []

    def test_republish_markdown_forwards_the_guard(self):
        client = CommentedFakeClient(
            initial=[_para("alpha")],
            comments=[_comment("c-1", "hold on")],
        )

        with pytest.raises(DestructiveRepublishError):
            republish_markdown(client, PAGE, "something completely different")

        assert client.ops == []

    def test_uncommented_page_proceeds_normally(self):
        """The guard refuses discussed pages, not every page."""
        client = CommentedFakeClient(initial=[_para("alpha")], comments=[])

        result = republish_block_tree(client, PAGE, [_para("CHANGED")])

        assert result.deleted_count == 1
        assert client.appended and client.deleted
        # It did have to ask, because this republish would write.
        assert len(client.comment_calls) == 1


# ---------------------------------------------------------------------------
# AC-3 — allow_destructive=True is the pre-cycle behaviour, exactly
# ---------------------------------------------------------------------------


class TestAllowDestructiveOverride:
    def test_override_matches_the_unguarded_result_and_skips_the_comment_read(self):
        blocks = [_para("alpha"), _para("beta")]
        new = [_para("alpha"), _para("CHANGED")]

        commented = CommentedFakeClient(
            initial=copy.deepcopy(blocks),
            comments=[_comment("c-1", "a"), _comment("c-2", "b")],
        )
        clean = CommentedFakeClient(initial=copy.deepcopy(blocks), comments=[])

        overridden = republish_block_tree(
            commented, PAGE, copy.deepcopy(new), allow_destructive=True
        )
        baseline = republish_block_tree(clean, PAGE, copy.deepcopy(new))

        # Same fixture, same counter start -> field-for-field identical results.
        assert overridden == baseline
        assert overridden.request_count == baseline.request_count
        assert overridden.deleted_count == baseline.deleted_count
        assert overridden.top_level_block_ids == baseline.top_level_block_ids
        assert commented.ops == clean.ops

        # The override never consults the comments endpoint.
        assert commented.comment_calls == []

    def test_override_on_republish_markdown(self):
        client = CommentedFakeClient(
            initial=[_para("alpha")], comments=[_comment("c-1", "a")]
        )

        result = republish_markdown(client, PAGE, "beta", allow_destructive=True)

        assert result.deleted_count == 1
        assert client.comment_calls == []


# ---------------------------------------------------------------------------
# AC-4 — the caller-supplied protected predicate, and no workspace in the library
# ---------------------------------------------------------------------------


class TestProtectedPredicate:
    def test_true_predicate_refuses_a_comment_free_page(self):
        client = CommentedFakeClient(initial=[_para("alpha")], comments=[])

        with pytest.raises(DestructiveRepublishError) as excinfo:
            republish_block_tree(
                client, PAGE, [_para("CHANGED")], protected=lambda pid: True
            )

        assert excinfo.value.trigger == "protected"
        assert excinfo.value.discussion_count == 0
        assert PAGE_ID in str(excinfo.value)
        assert client.ops == []
        # A protected refusal is decided without a network call.
        assert client.comment_calls == []

    def test_false_predicate_proceeds_on_a_comment_free_page(self):
        client = CommentedFakeClient(initial=[_para("alpha")], comments=[])

        result = republish_block_tree(
            client, PAGE, [_para("CHANGED")], protected=lambda pid: False
        )

        assert result.deleted_count == 1
        assert client.appended

    def test_predicate_receives_the_extracted_page_id(self):
        seen: list[str] = []
        client = CommentedFakeClient(initial=[_para("alpha")], comments=[])

        republish_block_tree(
            client,
            PAGE,
            [_para("CHANGED")],
            protected=lambda pid: bool(seen.append(pid)),
        )

        assert seen == [PAGE_ID]

    def test_predicate_is_not_consulted_when_nothing_would_be_written(self):
        blocks = [_para("alpha")]
        client = CommentedFakeClient(initial=blocks, comments=[])

        result = republish_block_tree(
            client,
            PAGE,
            copy.deepcopy(blocks),
            protected=lambda pid: pytest.fail(
                "protected must not be consulted on a zero-write republish"
            ),
        )

        assert result.request_count == 0

    def test_predicate_is_not_consulted_under_allow_destructive(self):
        client = CommentedFakeClient(initial=[_para("alpha")], comments=[])

        republish_block_tree(
            client,
            PAGE,
            [_para("CHANGED")],
            allow_destructive=True,
            protected=lambda pid: pytest.fail("override must bypass the guard"),
        )


class TestLibraryCarriesNoWorkspace:
    """The mechanism ships here; one workspace's schema does not (spec D-1/D-4).

    Two separate assertions, because the two facts have different strengths:

    - **The ids appear nowhere at all**, in code or in prose. A raw text scan.
    - **The property names are never used as values.** Scanning raw text for
      ``"Next"`` would red on the sentence in ``revise.py`` that *explains* why
      ``"Next"`` is not a default, and on a pre-existing ``Filter.checkbox``
      docstring example — prose about a name is not a binding to it. So the
      names are checked against every string constant the parser sees outside a
      docstring, which is exactly where a hardcoded schema binding would have to
      live to do any harm.
    """

    _PACKAGE = Path(__file__).resolve().parents[1] / "notion_ops"
    _WORKSPACE_IDS = (
        "2d8d371a-79f4-805f-acc8-000b8e17b621",  # AgenticOS Atoms data source
        "2d8d371a-79f4-80b0-8fbd-d8f1ab5a56f7",  # AgenticOS Atoms database
        "2d8d371a",                              # either, however written
    )
    _SCHEMA_NAMES = frozenset({"Atoms", "Next", "Previous", "Archived"})

    def _sources(self) -> list[Path]:
        files = sorted(self._PACKAGE.rglob("*.py"))
        assert files, "no library sources found — the scan would be vacuous"
        return files

    def test_no_agenticos_id_anywhere_in_the_package(self):
        offenders = [
            f"{path}: {needle}"
            for path in self._sources()
            for needle in self._WORKSPACE_IDS
            if needle in path.read_text()
        ]
        assert offenders == [], f"AgenticOS workspace id in the library: {offenders}"

    def test_no_atoms_property_name_used_as_a_string_constant(self):
        offenders: list[str] = []
        for path in self._sources():
            tree = ast.parse(path.read_text())
            docstrings = {
                id(node.body[0].value)
                for node in ast.walk(tree)
                if isinstance(
                    node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
                )
                and node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            }
            for node in ast.walk(tree):
                if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                    continue
                if id(node) in docstrings:
                    continue
                if node.value in self._SCHEMA_NAMES:
                    offenders.append(f"{path.name}:{node.lineno}: {node.value!r}")
        assert offenders == [], (
            "an AgenticOS property name is used as a value in the library "
            f"(it belongs in the hub's RevisionSchema binding): {offenders}"
        )

    def test_the_scan_can_fail(self, tmp_path):
        """Anti-vacuity: the AST scan finds a planted binding."""
        planted = tmp_path / "planted.py"
        planted.write_text('"""Docstring mentioning Next is fine."""\nX = {"Next": 1}\n')
        tree = ast.parse(planted.read_text())
        found = [
            n.value
            for n in ast.walk(tree)
            if isinstance(n, ast.Constant)
            and isinstance(n.value, str)
            and n.value in self._SCHEMA_NAMES
        ]
        assert found == ["Next"]


# ---------------------------------------------------------------------------
# AC-5 — a no-op is neither refused nor charged a request
# ---------------------------------------------------------------------------


class TestNoOpBypassesTheGuard:
    def test_identical_content_on_a_commented_page_is_a_silent_zero_write(self):
        blocks = [_para("alpha"), _para("beta"), _para("gamma")]
        client = CommentedFakeClient(
            initial=blocks,
            comments=[
                _comment("c-1", "one"),
                _comment("c-2", "two"),
                _comment("c-3", "three"),
            ],
        )
        before = client.top_ids

        result = republish_block_tree(client, PAGE, copy.deepcopy(blocks))

        assert result.request_count == 0
        assert result.deleted_count == 0
        assert result.top_level_block_ids == before
        assert client.top_ids == before
        assert client.ops == []
        # The point of AC-5: the guard sits after the diff, so an idempotent
        # re-publish costs no comments request at all.
        assert client.comment_calls == []


# ---------------------------------------------------------------------------
# AC-6 — the refusal is caused by the guard (non-vacuity)
# ---------------------------------------------------------------------------


class TestGuardNonVacuity:
    """Neutralize the guard; the refusals must vanish and the writes reappear.

    This is the in-repo companion to ``mutations.md``'s M-1 (delete the guard
    call from ``republish_block_tree``) and M-2 (flip ``allow_destructive``'s
    default to ``True``). If either mutation left the suite green, the tests
    above would be asserting something other than the guard.
    """

    def test_removing_the_guard_removes_the_refusal(self, monkeypatch):
        client = CommentedFakeClient(
            initial=[_para("alpha")], comments=[_comment("c-1", "a")]
        )
        monkeypatch.setattr(
            "notion_ops.utils.publish._guard_destructive_republish",
            lambda *a, **k: None,
        )

        result = republish_block_tree(client, PAGE, [_para("CHANGED")])

        assert result.deleted_count == 1
        assert client.appended and client.deleted

    def test_default_of_allow_destructive_is_what_makes_the_guard_reachable(self):
        """M-2: with the default flipped, AC-2's call would not refuse."""
        client = CommentedFakeClient(
            initial=[_para("alpha")], comments=[_comment("c-1", "a")]
        )

        # The mutation's behaviour, expressed as an explicit argument.
        result = republish_block_tree(
            client, PAGE, [_para("CHANGED")], allow_destructive=True
        )

        assert result.deleted_count == 1
        # ...and the shipped default is the opposite, which is what AC-2 relies on.
        assert republish_block_tree.__kwdefaults__["allow_destructive"] is False
        assert republish_markdown.__kwdefaults__["allow_destructive"] is False
