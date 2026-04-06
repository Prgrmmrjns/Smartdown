"""Pydantic request/response models."""
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from smartdown.notion_tokens import normalize_notion_integration_token


class AgentChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1)


class AgentRequest(BaseModel):
    document_id: str
    provider: Literal["mistral", "ollama"] = "mistral"
    model: str | None = Field(
        default=None,
        description="Mistral: mistral-small-latest or mistral-large-latest. "
        "Ollama: full name from `ollama list` (e.g. qwen3.5:latest).",
    )
    messages: list[AgentChatMessage] = Field(..., min_length=1)
    current_markdown: str | None = Field(
        default=None,
        description="Current editor content (may be empty if user hasn't written anything yet).",
    )
    instruction_prefix: str | None = Field(
        default=None,
        description="Standing instructions prepended to every user message for the model.",
    )
    apply_to_document: bool = Field(
        default=True,
        description="If true, stream/return markdown for the editor; if false, chat-only answer.",
    )
    mistral_api_key: str | None = Field(
        default=None,
        max_length=512,
        description="Required for Mistral: user's API key from the browser (not read from server env).",
    )


class ConvertFromUrlBody(BaseModel):
    url: str = Field(..., min_length=8, max_length=2048)
    strip_page_numbers: bool = True
    strip_citations: bool = False
    equation_handling: Literal["markdown", "image"] = "markdown"
    math_inline_code: bool = True
    extract_markdown: bool = Field(
        default=False,
        description="If false, only store the PDF for preview; run POST /api/extract-markdown later.",
    )


class ExtractMarkdownBody(BaseModel):
    document_id: str = Field(..., min_length=1)
    strip_page_numbers: bool = True
    strip_citations: bool = False
    equation_handling: Literal["markdown", "image"] = "markdown"
    math_inline_code: bool = True


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
    """Push current Markdown to a Notion database page (images uploaded via Notion file API)."""

    notion_token: str = Field(
        ...,
        min_length=8,
        description="Internal integration secret (starts with secret_ or ntn_).",
    )
    database_id: str = Field(
        ...,
        min_length=8,
        max_length=2048,
        description="Target database: paste Notion URL or raw UUID.",
    )
    title: str | None = Field(
        default=None,
        max_length=2000,
        description="Page title in the database; defaults from first # heading or 'Document'.",
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
        description="Database property values by column name (strings; mapped server-side by type).",
    )

    @field_validator("notion_token", mode="before")
    @classmethod
    def _validate_notion_token(cls, v: object) -> object:
        if isinstance(v, str):
            return normalize_notion_integration_token(v)
        return v


class NotionInspectRequest(BaseModel):
    notion_token: str = Field(..., min_length=8)
    database_id: str = Field(..., min_length=8, max_length=2048)
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
