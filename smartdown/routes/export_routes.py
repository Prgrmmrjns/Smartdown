"""Binary / file export endpoints."""

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response

from smartdown.docx_export import markdown_to_docx_bytes
from smartdown.fs_utils import safe_filename_base
from smartdown.models import DocxExportRequest


def register(app: FastAPI) -> None:
    @app.post("/api/export-docx")
    async def api_export_docx(body: DocxExportRequest):
        try:
            data = markdown_to_docx_bytes(
                body.markdown,
                images=body.images,
                include_images=body.include_images,
                title=body.title,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)) from e
        base = safe_filename_base(body.filename_base or "document")
        fn = f"{base}.docx"
        return Response(
            content=data,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f'attachment; filename="{fn}"'},
        )
