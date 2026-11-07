from overseas_story_producer.core import Case, Fact
import pytest


@pytest.fixture
def case() -> Case:
    return Case(
        id="11111111-1111-4111-8111-111111111111",
        title="Founder story",
        title_zh="一个独立开发者的选择",
        source_url="https://example.org/founder",
        synopsis_zh="一位创业者探索个人产品。",
        body_excerpt="The founder created a product.",
        facts=tuple(Fact(
            id=f"{i:08d}-1111-4111-8111-111111111111",
            kind="milestone", original=f"Evidence {i}",
            chinese=f"证据 {i}", evidence=f"Evidence url and quote {i}",
            self_stated=(i % 2 == 0),
        ) for i in range(1, 7)),
    )


@pytest.fixture
def model_response(case):
    text = "当这位开发者决定走出熟悉的工作节奏时，他首先面对的不是技术，而是不确定性。他曾尝试理解用户到底需要什么，也反复反思自己能承担怎样的风险。在原始材料中，这些经历只是零散的记载，因此我们需要小心分辨公开可见的信息和当事人的自述。" * 4
    return {
        "title": "一个开发者如何走出第一步",
        "body_markdown": "# 一个开发者如何走出第一步\n\n" + text
                         + "\n\n## 起点\n\n" + text
                         + "\n\n## 困难与选择\n\n" + text
                         + "\n\n## 转折之后\n\n" + text
                         + "\n\n## 资料来源\n\nhttps://example.org/founder",
        "facts_used": [fact.id for fact in case.facts],
        "uncertainties": ["需要人工确认时间线。"],
    }


@pytest.fixture
def model_review():
    return {"dimensions": {
        "character": 19, "conflict": 23, "decision": 19,
        "outcome": 14, "traceability": 14, "readability": 5,
    }, "issues": ["可压缩一个重复段落。"], "unsupported_claims": [],
        "hard_fail": False, "hard_fail_reason": ""}

