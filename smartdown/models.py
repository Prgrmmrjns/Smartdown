"""Pydantic request/response models."""
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from smartdown.notion_tokens import normalize_notion_integration_token


def _notion_merge_legacy_database_id_into_page_url(data: object) -> object:
    """Cached/old clients may still POST database_id; map to page_url before validation."""
    if isinstance(data, dict):
        d = dict(data)
        if not str(d.get("page_url") or "").strip():
            alt = str(d.get("database_id") or "").strip()
            if alt:
                d["page_url"] = alt
        return d
    return data


class AgentChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1)


class AgentBlockExcerpt(BaseModel):
    """One Markdown slice from the document."""

    index: int = Field(..., ge=0, le=4096)
    markdown: str = Field(..., min_length=1, max_length=120_000)


class AgentBlockNoteRequest(BaseModel):
    """Generate one note bullet from one or more selected paper blocks."""

    document_id: str = Field(..., min_length=1)
    block: AgentBlockExcerpt | None = Field(
        default=None,
        description="Single excerpt; omit when sending non-empty blocks.",
    )
    blocks: list[AgentBlockExcerpt] | None = Field(
        default=None,
        max_length=32,
        description="Multiple excerpts combined into one note; omit when sending block.",
    )
    format_instructions: str = Field(
        default="",
        max_length=4000,
        description="User-defined note formatting rules.",
    )
    instruction_prefix: str | None = Field(
        default=None,
        max_length=4000,
        description="Optional standing note instructions applied to every bullet generation.",
    )
    provider: Literal["mistral", "ollama"] = "mistral"
    model: str | None = None
    mistral_api_key: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def _block_or_blocks(self) -> "AgentBlockNoteRequest":
        if self.blocks is not None and len(self.blocks) > 0:
            return self
        if self.block is not None:
            return self
        raise ValueError("Provide 'block' or a non-empty 'blocks' array.")


class AgentBlockClarifyRequest(BaseModel):
    """Ask a clarifying question about one or more selected paper blocks."""

    document_id: str = Field(..., min_length=1)
    block: AgentBlockExcerpt | None = Field(
        default=None,
        description="Single excerpt; omit when sending non-empty blocks.",
    )
    blocks: list[AgentBlockExcerpt] | None = Field(
        default=None,
        max_length=32,
        description="Multiple excerpts as one combined context.",
    )
    messages: list[AgentChatMessage] = Field(..., min_length=1)
    provider: Literal["mistral", "ollama"] = "mistral"
    model: str | None = None
    mistral_api_key: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def _block_or_blocks_clarify(self) -> "AgentBlockClarifyRequest":
        if self.blocks is not None and len(self.blocks) > 0:
            return self
        if self.block is not None:
            return self
        raise ValueError("Provide 'block' or a non-empty 'blocks' array.")


class ConvertFromUrlBody(BaseModel):
    url: str = Field(..., min_length=8, max_length=2048)
    extract_markdown: bool = Field(
        default=False,
        description="If false, only store the PDF for preview; run POST /api/extract-markdown later.",
    )
    mistral_api_key: str | None = Field(default=None, max_length=512)


class ExtractMarkdownBody(BaseModel):
    document_id: str = Field(..., min_length=1)
    mistral_api_key: str | None = Field(default=None, max_length=512)


class DocxExportRequest(BaseModel):
    """Convert Markdown (+ optional embedded images) to a Word document."""

    markdown: str = Field(..., min_length=1)
    filename_base: str | None = Field(default=None, max_length=200)
    title: str | None = Field(default=None, max_length=2000)
    images: dict[str, str] = Field(
        default_factory=dict,
        description="Map images/filename.png -> standard base64 (same as /api/convert).",
    )
    include_images: bool = Field(default=True)


class NotionExportRequest(BaseModel):
    """Sync Markdown into an existing Notion page (images via Notion file API)."""

    notion_token: str = Field(
        default="",
        max_length=4096,
        description="Integration secret; omit if NOTION_INTEGRATION_SECRET is set on the server.",
    )
    page_url: str = Field(
        default="",
        max_length=2048,
        description="Page URL or ID; omit if NOTION_PAGE_ID is set on the server.",
    )

    @model_validator(mode="before")
    @classmethod
    def _legacy_page_target(cls, data: Any) -> Any:
        return _notion_merge_legacy_database_id_into_page_url(data)
    title: str | None = Field(
        default=None,
        max_length=2000,
        description="Page title property; defaults from first # heading or 'Document'.",
    )
    markdown: str = Field(..., min_length=1)
    images: dict[str, str] = Field(
        default_factory=dict,
        description="Map images/filename.png -> standard base64 (same keys as /api/convert).",
    )
    include_images: bool = Field(
        default=True,
        description="If false, remove ![](...) from markdown and do not upload images (tiny JSON body).",
    )
    extra_properties: dict[str, str] = Field(
        default_factory=dict,
        description="Page property values by name (strings; mapped server-side by type).",
    )

    @field_validator("notion_token", mode="before")
    @classmethod
    def _validate_notion_token(cls, v: object) -> object:
        if isinstance(v, str):
            return normalize_notion_integration_token(v)
        return v


class NotionInspectRequest(BaseModel):
    notion_token: str = Field(
        default="",
        max_length=4096,
        description="Omit if NOTION_INTEGRATION_SECRET is set on the server.",
    )
    page_url: str = Field(
        default="",
        max_length=2048,
        description="Omit if NOTION_PAGE_ID is set on the server.",
    )

    @model_validator(mode="before")
    @classmethod
    def _legacy_page_target_inspect(cls, data: Any) -> Any:
        return _notion_merge_legacy_database_id_into_page_url(data)
    markdown: str | None = Field(
        default=None,
        description="Optional: used to suggest URL-type fields from links in the text.",
    )

    @field_validator("notion_token", mode="before")
    @classmethod
    def _validate_notion_token(cls, v: object) -> object:
        if isinstance(v, str):
            return normalize_notion_integration_token(v)
        return v


class NotionPropertyDef(BaseModel):
    name: str = Field(..., min_length=1, max_length=500)
    type: str = Field(..., min_length=1, max_length=64)
    options: list[str] = Field(default_factory=list)


class NotionSuggestPropertiesRequest(BaseModel):
    markdown: str = Field(..., min_length=1)
    properties: list[NotionPropertyDef] = Field(..., min_length=1)
    user_hint: str | None = Field(default=None, max_length=2000)
    provider: Literal["mistral", "ollama"] = "mistral"
    model: str | None = None
    mistral_api_key: str | None = Field(default=None, max_length=512)
