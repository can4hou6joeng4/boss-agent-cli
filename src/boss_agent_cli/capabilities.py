"""平台能力分组的唯一真源。

`boss platforms --capability` 的反查取值与 `boss schema` 暴露的 choices
共用这里的定义，避免两处手工维护后漂移（Issue #1530）。
"""

READONLY_CAPABILITIES: tuple[str, ...] = (
	"search",
	"detail",
	"show",
	"history",
	"interviews",
	"recommend",
	"me",
	"status",
)
WRITE_CAPABILITIES: tuple[str, ...] = ("greet", "apply")
LOCAL_CAPABILITIES: tuple[str, ...] = ("shortlist", "stats", "config", "schema")

ALL_CAPABILITIES: tuple[str, ...] = (
	*READONLY_CAPABILITIES,
	*WRITE_CAPABILITIES,
	*LOCAL_CAPABILITIES,
)
