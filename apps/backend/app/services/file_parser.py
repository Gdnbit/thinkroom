"""File Parser Service

职责：从不同格式文件中提取纯文本内容。

支持格式：
- .txt / .md  → 直接读取
- .pdf        → pdfplumber（纯文本 PDF）→ 扫描件走视觉大模型
- .jpg/.png   → 视觉大模型（GPT-5 Vision）

长图处理策略：
- 图片高度超过 LONG_IMAGE_THRESHOLD 像素时，自动按 SLICE_HEIGHT 分块
- 每块单独送给视觉模型，结果按顺序拼接
- 多图上传时（same batch_hint），结果自动拼接为完整文档

降级策略：视觉模型不可用时，回落到 pytesseract OCR；pytesseract 不可用时返回 None。
"""
import re
import time
from pathlib import Path
from typing import Optional, List
from loguru import logger
from app.utils.document_parse_result import DocumentParseSegment

# 长图分块阈值（像素高度）
LONG_IMAGE_THRESHOLD = 2000
# 每块高度（像素），含 200px 重叠避免内容截断
SLICE_HEIGHT = 1800
SLICE_OVERLAP = 200

# 视觉模型 system prompt
VISION_SYSTEM_PROMPT = """你是一位专业的文档内容提取助手。
请将图片中的所有文字内容完整、准确地转录出来，保持原有的段落结构和格式。

核心要求：
1. 完整提取所有可见文字，包括标题、正文、数字、表格内容
2. 表格用 Markdown 格式保留结构（| 列1 | 列2 | 格式）
3. 如果图片是长图的一部分，正常提取当前可见内容即可
4. 这是逐字转录任务，不是摘要任务；不得概括、改写、合并或省略段落、脚注、图例、数字和重复内容
5. 只输出转录正文，不要添加“以下是提取结果”等前言或完成说明

对于无法直接转录为文字的特殊内容，使用以下格式添加结构化标注（插入在对应位置）：
- K线图/价格图表：[K线图：{资产名} {时间范围}，关键价位：{支撑位/阻力位/当前价}，趋势：{上涨/下跌/震荡}]
- 普通图表/数据图：[图表：{图表标题或描述，包含主要数据点和趋势}]
- 推特/微博截图：[社交媒体引用：{发布者} — {引用内容完整文字}]
- 新闻/报道截图：[新闻引用：{媒体名/来源} — {标题或核心内容}]
- 表情包/meme：[图片：{描述图片内容和含义}]
- 二维码/无法识别区域：[无法识别：{区域描述}]

原则：意思完整清晰优先，不要遗漏任何有信息量的内容。"""

# 微信聊天截图专用 Vision prompt — 要求严格保留 发言人+时间 结构
VISION_CHAT_PROMPT = """你是一位专业的微信聊天记录提取助手。
请将图片中的微信聊天内容完整、准确地转录出来。

**严格格式要求**（每条消息必须遵循此格式）：
```
发言人昵称  YYYY年M月D日 HH:MM
消息正文内容
```

核心规则：
1. 每条消息前必须有【发言人昵称】和【时间戳】，两者用两个空格分隔
2. 如果时间戳只有时分（如 16:31），请保留原样，不要补日期
3. 不同消息之间用空行分隔
4. 引用回复格式：在消息正文中用 `> 原消息发言人：原消息内容` 表示
5. 聊天中出现的图片/表情/视频：用 `[图片]` `[表情]` `[视频]` 占位
6. 如果图片顶部有"群聊的聊天记录"或日期标题，保留在最开头
7. 完整保留每个发言人的昵称（包括中英文混合昵称、括号备注等）
8. 不要遗漏任何发言人的任何消息

对于聊天中嵌入的非文字内容，使用结构化标注：
- 分享的文章/链接：[链接：{标题}]
- 分享的图片/图表：[图片]
- K线图：[K线图：{资产名} {时间范围}]
- 新闻截图：[新闻引用：{来源} — {标题}]"""


def _record_diagnostic(diagnostics: list[dict] | None, method: str, status: str, **fields) -> None:
    if diagnostics is None:
        return
    item = {"method": method, "status": status}
    for key, value in fields.items():
        if value is not None:
            item[key] = value
    diagnostics.append(item)


def _append_parse_segment(
    segment_collector: list[DocumentParseSegment] | None,
    *,
    content: str,
    method: str,
    source_name: str,
    page: int | None = None,
    ordinal: int | None = None,
    segment_type: str = "document",
) -> None:
    if segment_collector is None:
        return
    segment_collector.append(
        DocumentParseSegment(
            type=segment_type,
            content=content or "",
            method=method,
            page=page,
            ordinal=ordinal,
            source_name=source_name,
        )
    )


def extract_text(
    file_path: str,
    mime_type: str = "",
    progress_cb=None,
    vision_prompt: str = "",
    diagnostics: list[dict] | None = None,
    segment_collector: list[DocumentParseSegment] | None = None,
) -> Optional[str]:
    """
    从文件中提取纯文本内容。
    progress_cb: 可选回调 fn(current_page, total_pages) 用于追踪 PDF OCR 进度。
    vision_prompt: 可选的自定义 Vision prompt（用于聊天截图等场景）。
    返回 None 表示无法提取（格式不支持或依赖缺失）。
    """
    p = Path(file_path)
    if not p.exists():
        logger.warning(f"[FileParser] File not found: {file_path}")
        _record_diagnostic(
            diagnostics,
            "dispatch",
            "failed",
            reason_code="file_not_found",
            file_type=p.suffix.lower() or None,
        )
        return None

    suffix = p.suffix.lower()

    # ── 纯文本 ────────────────────────────────────────────
    if suffix in (".txt", ".md") or mime_type == "text/plain":
        return _read_text(p, diagnostics=diagnostics, segment_collector=segment_collector)

    # ── Word 文档 ─────────────────────────────────────────
    if suffix in (".docx", ".doc"):
        return _extract_docx(p, diagnostics=diagnostics, segment_collector=segment_collector)

    # ── PDF ──────────────────────────────────────────────
    if suffix == ".pdf" or mime_type == "application/pdf":
        return _extract_pdf(
            p,
            progress_cb=progress_cb,
            diagnostics=diagnostics,
            segment_collector=segment_collector,
        )

    # ── 图片 ─────────────────────────────────────────────
    if suffix in (".jpg", ".jpeg", ".png", ".webp") or mime_type.startswith("image/"):
        return _extract_image(
            p,
            vision_prompt=vision_prompt,
            diagnostics=diagnostics,
            segment_collector=segment_collector,
            progress_cb=progress_cb,
        )

    logger.info(f"[FileParser] Unsupported format: {suffix}")
    _record_diagnostic(
        diagnostics,
        "dispatch",
        "failed",
        reason_code="unsupported_format",
        file_type=suffix or None,
    )
    return None


def _read_text(
    p: Path,
    diagnostics: list[dict] | None = None,
    segment_collector: list[DocumentParseSegment] | None = None,
) -> Optional[str]:
    try:
        text = p.read_text(encoding="utf-8", errors="ignore").strip()
        if text:
            _record_diagnostic(diagnostics, "read_text", "success", chars=len(text))
            _append_parse_segment(
                segment_collector,
                content=text,
                method="read_text",
                source_name=p.name,
                ordinal=1,
            )
        else:
            _record_diagnostic(diagnostics, "read_text", "empty", reason_code="empty_text")
        return text if text else None
    except Exception as e:
        logger.warning(f"[FileParser] Text read error: {e}")
        _record_diagnostic(
            diagnostics,
            "read_text",
            "failed",
            reason_code="exception",
            error_type=e.__class__.__name__,
            error=str(e)[:300],
        )
        return None


def _extract_docx(
    p: Path,
    diagnostics: list[dict] | None = None,
    segment_collector: list[DocumentParseSegment] | None = None,
) -> Optional[str]:
    """提取 Word 文档（.docx）文本内容"""
    try:
        import docx
        doc = docx.Document(str(p))
        parts = []
        for para in doc.paragraphs:
            if para.text.strip():
                parts.append(para.text.strip())
        # 提取表格内容
        for table in doc.tables:
            for row in table.rows:
                row_text = ' | '.join(cell.text.strip() for cell in row.cells if cell.text.strip())
                if row_text:
                    parts.append(row_text)
        result = '\n'.join(parts).strip()
        if result:
            logger.info(f"[FileParser] DOCX extracted {len(result)} chars from {p.name}")
            _record_diagnostic(diagnostics, "python_docx", "success", chars=len(result))
            _append_parse_segment(
                segment_collector,
                content=result,
                method="python_docx",
                source_name=p.name,
                ordinal=1,
            )
            return result
        _record_diagnostic(diagnostics, "python_docx", "empty", reason_code="empty_text")
        return None
    except ImportError:
        logger.warning("[FileParser] python-docx not installed")
        _record_diagnostic(diagnostics, "python_docx", "failed", reason_code="dependency_missing")
        return None
    except Exception as e:
        logger.warning(f"[FileParser] DOCX extract error: {e}")
        _record_diagnostic(
            diagnostics,
            "python_docx",
            "failed",
            reason_code="exception",
            error_type=e.__class__.__name__,
            error=str(e)[:300],
        )
        return None


def _extract_pdf(
    p: Path,
    progress_cb=None,
    diagnostics: list[dict] | None = None,
    segment_collector: list[DocumentParseSegment] | None = None,
) -> Optional[str]:
    """Extract PDF text and route image-only pages through bounded OCR."""
    segment_start = len(segment_collector) if segment_collector is not None else 0
    diagnostic_start = len(diagnostics) if diagnostics is not None else 0
    pdfium_text = _extract_pdf_text_pdfium(
        p,
        progress_cb=progress_cb,
        diagnostics=diagnostics,
        segment_collector=segment_collector,
    )
    if pdfium_text:
        return pdfium_text
    if segment_collector is not None:
        del segment_collector[segment_start:]

    pdfium_attempts = (diagnostics or [])[diagnostic_start:]
    if any(
        item.get("method") == "pdfium_text" and item.get("status") == "empty"
        for item in pdfium_attempts
        if isinstance(item, dict)
    ):
        return _pdf_ocr(
            p,
            progress_cb=progress_cb,
            diagnostics=diagnostics,
            segment_collector=segment_collector,
        )

    try:
        import pdfplumber  # type: ignore
        texts = []
        nonempty_pages = 0
        empty_pages = []
        with pdfplumber.open(str(p)) as pdf:
            for page_number, page in enumerate(pdf.pages, start=1):
                page_text = (page.extract_text() or "").strip()
                if page_text:
                    texts.append(page_text)
                    nonempty_pages += 1
                else:
                    empty_pages.append(page_number)
                _append_parse_segment(
                    segment_collector,
                    content=page_text,
                    method="pdfplumber_text",
                    source_name=p.name,
                    page=page_number,
                    ordinal=page_number,
                )
            page_count = len(pdf.pages)
        result = "\n\n".join(texts).strip()
        if result:
            logger.info(f"[FileParser] PDF extracted {len(result)} chars from {p.name}")
            _record_diagnostic(
                diagnostics,
                "pdfplumber_text",
                "success",
                chars=len(result),
                pages=page_count,
                nonempty_pages=nonempty_pages,
                empty_pages=empty_pages or None,
            )
            return result
        # pdfplumber 无文字时尝试 OCR
        logger.info(f"[FileParser] PDF has no text layer, trying OCR: {p.name}")
        _record_diagnostic(diagnostics, "pdfplumber_text", "empty", reason_code="no_text_layer", pages=page_count)
        if segment_collector is not None:
            del segment_collector[segment_start:]
        return _pdf_ocr(
            p,
            progress_cb=progress_cb,
            diagnostics=diagnostics,
            segment_collector=segment_collector,
        )
    except ImportError:
        logger.warning("[FileParser] pdfplumber not installed, falling back to OCR")
        _record_diagnostic(diagnostics, "pdfplumber_text", "failed", reason_code="dependency_missing")
        if segment_collector is not None:
            del segment_collector[segment_start:]
        return _pdf_ocr(
            p,
            progress_cb=progress_cb,
            diagnostics=diagnostics,
            segment_collector=segment_collector,
        )
    except Exception as e:
        logger.warning(f"[FileParser] PDF extract error: {e}")
        _record_diagnostic(
            diagnostics,
            "pdfplumber_text",
            "failed",
            reason_code="exception",
            error_type=e.__class__.__name__,
            error=str(e)[:300],
        )
        return None


def _pdf_page_visual_profile(page) -> tuple[float, int]:
    """Return the strongest image coverage ratio and vector drawing count."""
    page_area = max(1.0, float(page.rect.width) * float(page.rect.height))
    max_coverage = 0.0
    try:
        for image_info in page.get_image_info(xrefs=True):
            bbox = image_info.get("bbox") if isinstance(image_info, dict) else None
            if not bbox or len(bbox) != 4:
                continue
            width = max(0.0, float(bbox[2]) - float(bbox[0]))
            height = max(0.0, float(bbox[3]) - float(bbox[1]))
            max_coverage = max(max_coverage, min(1.0, width * height / page_area))
    except Exception as exc:
        logger.debug(f"[FileParser] PDF image-profile probe failed: {exc.__class__.__name__}")

    drawing_count = 0
    try:
        drawing_count = len(page.get_drawings())
    except Exception as exc:
        logger.debug(f"[FileParser] PDF drawing-profile probe failed: {exc.__class__.__name__}")
    return max_coverage, drawing_count


def _extract_pdf_text_pdfium(
    p: Path,
    progress_cb=None,
    diagnostics: list[dict] | None = None,
    segment_collector: list[DocumentParseSegment] | None = None,
) -> Optional[str]:
    """Fast text-layer extraction for large PDFs.

    pdfplumber can be slow on long reports. PDFium gives us page-level progress and
    keeps large text PDFs from looking stuck during ingestion.
    """
    try:
        from app.services.pdf_backend import open_document
    except ImportError:
        _record_diagnostic(diagnostics, "pdfium_text", "failed", reason_code="dependency_missing")
        return None

    try:
        from app.config import settings

        doc = open_document(str(p))
        total_pages = len(doc)
        page_records: list[dict] = []
        nonempty_pages = 0
        empty_pages = []
        text_layer_detected = False
        min_visual_coverage = min(
            1.0,
            max(0.0, float(getattr(settings, "DOCUMENT_HYBRID_MIN_VISUAL_COVERAGE", 0.15) or 0.0)),
        )
        min_drawing_count = max(
            0,
            int(getattr(settings, "DOCUMENT_HYBRID_MIN_DRAWING_COUNT", 50) or 0),
        )
        try:
            for idx, page in enumerate(doc, start=1):
                page_text = (page.get_text("text") or "").strip()
                visual_coverage = 0.0
                drawing_count = 0
                if page_text:
                    nonempty_pages += 1
                    text_layer_detected = True
                else:
                    empty_pages.append(idx)
                    visual_coverage, drawing_count = _pdf_page_visual_profile(page)
                page_records.append(
                    {
                        "page": idx,
                        "text": page_text,
                        "visual_coverage": visual_coverage,
                        "drawing_count": drawing_count,
                        "visual_only": bool(
                            not page_text
                            and (
                                visual_coverage >= min_visual_coverage
                                or drawing_count >= min_drawing_count
                            )
                        ),
                    }
                )
        finally:
            doc.close()

        if text_layer_detected:
            visual_pages = [record["page"] for record in page_records if record["visual_only"]]
            skipped_decorative_pages: list[int] = []
            if (
                bool(getattr(settings, "DOCUMENT_HYBRID_SKIP_DECORATIVE_EDGE_PAIR", True))
                and total_pages >= 20
                and visual_pages == [1, total_pages]
            ):
                skipped_decorative_pages = [1, total_pages]
            ocr_pages = [page for page in visual_pages if page not in skipped_decorative_pages]
            completed_without_ocr = total_pages - len(ocr_pages)
            if progress_cb:
                for completed_page in range(1, completed_without_ocr + 1):
                    progress_cb(completed_page, total_pages)

            native_text = "\n\n".join(
                record["text"] for record in page_records if record["text"]
            ).strip()
            _record_diagnostic(
                diagnostics,
                "pdfium_text",
                "success",
                chars=len(native_text),
                pages=total_pages,
                nonempty_pages=nonempty_pages,
                empty_pages=empty_pages or None,
                visual_only_pages=visual_pages or None,
                skipped_decorative_pages=skipped_decorative_pages or None,
            )

            ocr_segments: list[DocumentParseSegment] = []
            ocr_diagnostics: list[dict] = []
            if ocr_pages:
                _pdf_ocr(
                    p,
                    progress_cb=progress_cb,
                    diagnostics=ocr_diagnostics,
                    segment_collector=ocr_segments,
                    page_numbers=ocr_pages,
                    progress_start=completed_without_ocr,
                    progress_total=total_pages,
                    append_failure_warning=False,
                )
                if diagnostics is not None:
                    diagnostics.extend(ocr_diagnostics)

            ocr_by_page = {segment.page: segment for segment in ocr_segments}
            failed_pages = sorted(
                {
                    page
                    for item in ocr_diagnostics
                    for page in (item.get("failed_pages") or [])
                    if isinstance(page, int) and page > 0
                }
            )
            merged_texts = []
            for record in page_records:
                page_number = record["page"]
                if page_number in ocr_by_page:
                    page_text = ocr_by_page[page_number].content
                    method = "pdf_ocr"
                elif page_number in skipped_decorative_pages:
                    page_text = ""
                    method = "pdfium_decorative_skip"
                else:
                    page_text = record["text"]
                    method = "pdfium_text"
                if page_text:
                    merged_texts.append(page_text)
                _append_parse_segment(
                    segment_collector,
                    content=page_text,
                    method=method,
                    source_name=p.name,
                    page=page_number,
                    ordinal=page_number,
                )

            result = "\n\n".join(merged_texts).strip()
            model_calls = [
                call
                for item in ocr_diagnostics
                for call in (item.get("model_calls") or [])
                if isinstance(call, dict)
            ]
            if ocr_pages:
                _record_diagnostic(
                    diagnostics,
                    "pdf_hybrid",
                    "partial" if failed_pages else "success",
                    chars=len(result),
                    pages=total_pages,
                    native_pages=[record["page"] for record in page_records if record["text"]],
                    ocr_pages=ocr_pages,
                    failed_pages=failed_pages or None,
                    blank_pages=[
                        record["page"]
                        for record in page_records
                        if not record["text"] and not record["visual_only"]
                    ] or None,
                    skipped_decorative_pages=skipped_decorative_pages or None,
                    model_calls=model_calls or None,
                    visual_coverage_threshold=min_visual_coverage,
                    drawing_count_threshold=min_drawing_count,
                )
            logger.info(
                f"[FileParser] PDF page-aware extraction produced {len(result)} chars from {p.name}; "
                f"native={nonempty_pages}, ocr={len(ocr_pages)}, failed={len(failed_pages)}"
            )
            return result or None

        logger.info(f"[FileParser] PDF PDFium found no text layer: {p.name}")
        _record_diagnostic(diagnostics, "pdfium_text", "empty", reason_code="no_text_layer", pages=total_pages)
        return None
    except Exception as e:
        logger.warning(f"[FileParser] PDF PDFium extract error: {e}")
        _record_diagnostic(
            diagnostics,
            "pdfium_text",
            "failed",
            reason_code="exception",
            error_type=e.__class__.__name__,
            error=str(e)[:300],
        )
        return None


def _pdf_ocr(
    p: Path,
    progress_cb=None,
    diagnostics: list[dict] | None = None,
    segment_collector: list[DocumentParseSegment] | None = None,
    page_numbers: list[int] | None = None,
    progress_start: int = 0,
    progress_total: int | None = None,
    append_failure_warning: bool = True,
) -> Optional[str]:
    """Render and OCR PDF pages with bounded in-flight memory and stable ordering."""
    try:
        from app.services.pdf_backend import open_document
        from PIL import Image
        from app.config import settings
        from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

        Image.MAX_IMAGE_PIXELS = None
        doc = open_document(str(p))
        total_pages = len(doc)
        if total_pages == 0:
            doc.close()
            _record_diagnostic(diagnostics, "pdf_ocr", "empty", reason_code="empty_pdf", pages=0)
            return None

        selected_pages = (
            list(range(1, total_pages + 1))
            if page_numbers is None
            else sorted({page for page in page_numbers if isinstance(page, int) and 1 <= page <= total_pages})
        )
        if not selected_pages:
            doc.close()
            _record_diagnostic(
                diagnostics,
                "pdf_ocr",
                "empty",
                reason_code="no_pages_selected",
                pages=total_pages,
                selected_pages=[],
            )
            return None
        work_total = len(selected_pages)
        reported_total = max(work_total, int(progress_total or work_total))

        max_workers = max(
            1,
            min(work_total, int(getattr(settings, "DOCUMENT_OCR_MAX_WORKERS", 3) or 3)),
        )
        openrouter_client = _build_openrouter_vision_client(settings)
        model_calls: list[dict] = []
        page_results: dict[int, str] = {}
        page_slice_failures: dict[int, list[int]] = {}
        failed_pages: list[int] = []
        completed = 0
        next_page_index = 0

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            pending = {}
            while next_page_index < work_total or pending:
                while next_page_index < work_total and len(pending) < max_workers:
                    page_number = selected_pages[next_page_index]
                    try:
                        image = _render_pdf_page_image(doc[page_number - 1], settings)
                    except Exception as exc:
                        logger.warning(f"[FileParser] PDF page {page_number} render failed: {exc}")
                        page_results[page_number] = ""
                        failed_pages.append(page_number)
                        completed += 1
                        if progress_cb:
                            progress_cb(progress_start + completed, reported_total)
                        next_page_index += 1
                        continue
                    future = pool.submit(
                        _ocr_pdf_page_image,
                        image,
                        settings,
                        openrouter_client=openrouter_client,
                        trace_collector=model_calls,
                        page_number=page_number,
                    )
                    pending[future] = page_number
                    next_page_index += 1

                if not pending:
                    continue
                done, _ = wait(tuple(pending), return_when=FIRST_COMPLETED)
                for future in done:
                    page_number = pending.pop(future)
                    try:
                        raw_page_result = future.result()
                    except Exception as exc:
                        logger.warning(f"[FileParser] PDF OCR page {page_number} failed: {exc}")
                        raw_page_result = None
                    if isinstance(raw_page_result, dict):
                        page_text = str(raw_page_result.get("text") or "").strip()
                        failed_slices = sorted(
                            value
                            for value in raw_page_result.get("failed_slices", [])
                            if isinstance(value, int) and value > 0
                        )
                    else:
                        page_text = str(raw_page_result or "").strip()
                        failed_slices = []
                    page_results[page_number] = page_text
                    if not page_text or failed_slices:
                        failed_pages.append(page_number)
                    if failed_slices:
                        page_slice_failures[page_number] = failed_slices
                    completed += 1
                    if progress_cb:
                        progress_cb(progress_start + completed, reported_total)
                    logger.info(
                        f"[FileParser] PDF OCR completed {completed}/{work_total} "
                        f"(source page {page_number})"
                    )
        doc.close()

        failed_pages = sorted(set(failed_pages))
        texts = []
        for page_number in selected_pages:
            page_text = page_results.get(page_number, "")
            if page_text:
                texts.append(page_text)
            _append_parse_segment(
                segment_collector,
                content=page_text,
                method="pdf_ocr",
                source_name=p.name,
                page=page_number,
                ordinal=page_number,
            )
        result = "\n\n".join(texts).strip()
        if not result:
            _record_diagnostic(
                diagnostics,
                "pdf_ocr",
                "empty",
                reason_code="no_ocr_text",
                pages=total_pages,
                selected_pages=selected_pages if page_numbers is not None else None,
                completed_pages=completed,
                failed_pages=failed_pages or None,
                max_workers=max_workers,
            )
            return None
        if failed_pages and append_failure_warning:
            warning = f"\n\n[⚠️ PDF 共 {total_pages} 页，其中第 {', '.join(str(p) for p in failed_pages)} 页提取失败，内容可能不完整]"
            result += warning
            logger.warning(f"[FileParser] PDF partial OCR: {len(failed_pages)}/{total_pages} pages failed for {p.name}")
        _record_diagnostic(
            diagnostics,
            "pdf_ocr",
            "partial" if failed_pages else "success",
            chars=len(result),
            pages=total_pages,
            selected_pages=selected_pages if page_numbers is not None else None,
            completed_pages=completed,
            failed_pages=failed_pages or None,
            page_slice_failures=page_slice_failures or None,
            max_workers=max_workers,
            model_calls=sorted(
                model_calls,
                key=lambda item: (
                    int(item.get("page") or 0),
                    int(item.get("slice") or 0),
                    int(item.get("attempt") or 0),
                ),
            ) or None,
        )
        return result
    except ImportError:
        logger.warning("[FileParser] PDFium not installed, PDF OCR unavailable")
        _record_diagnostic(diagnostics, "pdf_ocr", "failed", reason_code="dependency_missing")
        return None
    except Exception as e:
        logger.warning(f"[FileParser] PDF OCR error: {e}")
        _record_diagnostic(
            diagnostics,
            "pdf_ocr",
            "failed",
            reason_code="exception",
            error_type=e.__class__.__name__,
            error=str(e)[:300],
        )
        return None


def _render_pdf_page_image(page, settings):
    requested_dpi = max(72, int(getattr(settings, "DOCUMENT_OCR_PAGE_DPI", 180) or 180))
    rect = page.rect
    projected_width = max(1.0, rect.width * requested_dpi / 72.0)
    projected_height = max(1.0, rect.height * requested_dpi / 72.0)
    max_dimension = max(projected_width, projected_height)
    dpi = requested_dpi
    if max_dimension > 3000:
        dpi = max(72, int(requested_dpi * 3000 / max_dimension))
    return page.render_image(dpi)


def _ocr_pdf_page_image(
    img,
    settings,
    openrouter_client=None,
    trace_collector: list[dict] | None = None,
    page_number: int | None = None,
) -> dict:
    threshold = max(
        LONG_IMAGE_THRESHOLD,
        int(getattr(settings, "DOCUMENT_OCR_PAGE_SLICE_THRESHOLD", 3600) or 3600),
    )
    slices = _slice_image(img, SLICE_HEIGHT, SLICE_OVERLAP) if img.height > threshold else [img]
    texts = []
    failed_slices = []
    has_direct_vision_key = bool(
        getattr(settings, 'FUSION_API_KEY', '')
        or getattr(settings, 'COMMITTEE_API_KEY_B', '')
        or getattr(settings, 'OPENAI_API_KEY', '')
    )
    for idx, image in enumerate(slices, start=1):
        text = _call_vision_api(image, settings) if has_direct_vision_key else None
        if not text:
            text = _call_openrouter_vision_api(
                image,
                settings,
                VISION_SYSTEM_PROMPT,
                client=openrouter_client,
                trace_collector=trace_collector,
                trace_context={
                    "unit_type": "pdf_page_slice",
                    "page": page_number,
                    "slice": idx,
                },
                primary_model=str(
                    getattr(settings, "DOCUMENT_PDF_OCR_MODEL", "google/gemini-3.5-flash")
                    or "google/gemini-3.5-flash"
                ),
                fallback_model=str(
                    getattr(
                        settings,
                        "DOCUMENT_PDF_OCR_FALLBACK_MODEL",
                        "google/gemini-3.1-flash-lite",
                    )
                    or ""
                ),
                primary_reasoning_effort=str(
                    getattr(settings, "DOCUMENT_PDF_OCR_REASONING_EFFORT", "minimal")
                    or "minimal"
                ),
                fallback_reasoning_effort=str(
                    getattr(settings, "DOCUMENT_PDF_OCR_FALLBACK_REASONING_EFFORT", "none")
                    or "none"
                ),
            )
        if not text:
            text = _call_tesseract_image(image)
        if text:
            texts.append(text.strip())
        else:
            failed_slices.append(idx)
    if not texts:
        return {"text": "", "slices": len(slices), "failed_slices": failed_slices}
    merged = dedup_adjacent_ocr_texts(texts) if len(texts) > 1 else _strip_ocr_preamble(texts[0])
    return {"text": merged, "slices": len(slices), "failed_slices": failed_slices}


def _call_tesseract_image(img) -> Optional[str]:
    try:
        import shutil
        import pytesseract

        tesseract_cmd = str(getattr(pytesseract.pytesseract, "tesseract_cmd", "tesseract") or "tesseract")
        if not Path(tesseract_cmd).is_file() and shutil.which(tesseract_cmd) is None:
            return None
        return (pytesseract.image_to_string(img.convert("L"), lang="chi_sim+eng") or "").strip() or None
    except Exception:
        return None


def _extract_image(
    p: Path,
    vision_prompt: str = "",
    diagnostics: list[dict] | None = None,
    segment_collector: list[DocumentParseSegment] | None = None,
    progress_cb=None,
) -> Optional[str]:
    """使用视觉大模型提取图片文本，支持长图自动分块。
    降级链：GPT-5 Vision → OpenRouter Vision (Gemini) → pytesseract → None
    vision_prompt: 自定义 prompt（如 VISION_CHAT_PROMPT 用于聊天截图），为空则用默认 VISION_SYSTEM_PROMPT。
    """
    # 尝试 GPT-5 直连 Vision
    direct_details: dict = {}
    result = _extract_image_vision(p, progress_cb=progress_cb, details=direct_details)
    if result:
        direct_status = "partial" if direct_details.get("failed_slices") else "success"
        _record_diagnostic(diagnostics, "openai_vision", direct_status, chars=len(result), **direct_details)
        _append_parse_segment(
            segment_collector,
            content=result,
            method="openai_vision",
            source_name=p.name,
            ordinal=1,
            segment_type="image",
        )
        return result
    _record_diagnostic(
        diagnostics,
        "openai_vision",
        "empty",
        reason_code=direct_details.pop("reason_code", "no_text_or_unavailable"),
        **direct_details,
    )

    # 降级：OpenRouter Vision（Gemini 3 Flash 等多模态模型）
    openrouter_details: dict = {}
    result = _extract_image_openrouter(
        p,
        vision_prompt=vision_prompt,
        progress_cb=progress_cb,
        details=openrouter_details,
    )
    if result:
        openrouter_status = "partial" if openrouter_details.get("failed_slices") else "success"
        _record_diagnostic(
            diagnostics,
            "openrouter_vision",
            openrouter_status,
            chars=len(result),
            **openrouter_details,
        )
        _append_parse_segment(
            segment_collector,
            content=result,
            method="openrouter_vision",
            source_name=p.name,
            ordinal=1,
            segment_type="image",
        )
        return result
    _record_diagnostic(
        diagnostics,
        "openrouter_vision",
        "empty",
        reason_code=openrouter_details.pop("reason_code", "no_text_or_unavailable"),
        **openrouter_details,
    )

    # 降级：pytesseract
    tesseract_details: dict = {}
    result = _extract_image_ocr(p, progress_cb=progress_cb, details=tesseract_details)
    if result:
        tesseract_status = "partial" if tesseract_details.get("failed_slices") else "success"
        _record_diagnostic(
            diagnostics,
            "tesseract_ocr",
            tesseract_status,
            chars=len(result),
            **tesseract_details,
        )
        _append_parse_segment(
            segment_collector,
            content=result,
            method="tesseract_ocr",
            source_name=p.name,
            ordinal=1,
            segment_type="image",
        )
    else:
        reason_code = tesseract_details.pop("reason_code", "no_text_or_unavailable")
        status = "failed" if reason_code in {"dependency_missing", "runtime_missing"} else "empty"
        _record_diagnostic(
            diagnostics,
            "tesseract_ocr",
            status,
            reason_code=reason_code,
            **tesseract_details,
        )
    return result


def _extract_image_openrouter(
    p: Path,
    vision_prompt: str = "",
    progress_cb=None,
    details: dict | None = None,
) -> Optional[str]:
    """通过 OpenRouter 多模态模型（Gemini 3 Flash 等）提取图片文本，支持长图分块。
    vision_prompt: 自定义 prompt。为空则使用默认 VISION_SYSTEM_PROMPT。
    """
    try:
        from PIL import Image
        from app.config import settings

        api_key = getattr(settings, 'OPENROUTER_API_KEY', '') or getattr(settings, 'OPENROUTER_API_KEY_EXTRACTION', '')
        if not api_key:
            if details is not None:
                details["reason_code"] = "credential_unavailable"
            return None

        img = Image.open(str(p)).convert("RGB")
        w, h = img.size

        # 长图分块
        if h > LONG_IMAGE_THRESHOLD:
            slices = _slice_image(img, SLICE_HEIGHT, SLICE_OVERLAP)
            logger.info(f"[FileParser] Long image ({h}px), split into {len(slices)} slices via OpenRouter: {p.name}")
        else:
            slices = [img]

        client = _build_openrouter_vision_client(settings)
        if client is None:
            if details is not None:
                details["reason_code"] = "client_unavailable"
            return None

        from concurrent.futures import ThreadPoolExecutor, as_completed
        max_workers = max(1, min(len(slices), int(getattr(settings, "DOCUMENT_OCR_MAX_WORKERS", 3) or 3)))
        texts_with_idx = []
        failed_slices = []
        model_calls: list[dict] = []
        completed = 0
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(
                    _call_openrouter_vision_api,
                    image,
                    settings,
                    vision_prompt or VISION_SYSTEM_PROMPT,
                    client=client,
                    trace_collector=model_calls,
                    trace_context={"unit_type": "image_slice", "slice": idx + 1},
                ): idx
                for idx, image in enumerate(slices)
            }
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    slice_text = future.result()
                except Exception as e:
                    slice_text = None
                    logger.warning(f"[FileParser] OpenRouter vision slice {idx+1} failed: {e}")
                if slice_text:
                    texts_with_idx.append((idx, slice_text.strip()))
                    logger.info(
                        f"[FileParser] OpenRouter vision slice {idx+1}/{len(slices)}: "
                        f"{len(slice_text)} chars"
                    )
                else:
                    failed_slices.append(idx + 1)
                completed += 1
                if progress_cb:
                    progress_cb(completed, len(slices))

        failed_slices.sort()
        texts_with_idx.sort(key=lambda item: item[0])
        if details is not None:
            details.update(
                {
                    "width": w,
                    "height": h,
                    "slices": len(slices),
                    "completed_slices": len(texts_with_idx),
                    "failed_slices": failed_slices,
                    "max_workers": max_workers,
                    "model_calls": sorted(
                        model_calls,
                        key=lambda item: (
                            int(item.get("slice") or 0),
                            int(item.get("attempt") or 0),
                        ),
                    ),
                }
            )

        if texts_with_idx:
            ordered_texts = [text for _, text in texts_with_idx]
            if len(ordered_texts) > 1:
                result = dedup_adjacent_ocr_texts(ordered_texts)
            else:
                result = _strip_ocr_preamble(ordered_texts[0])
            if failed_slices:
                warning = f"\n\n[⚠️ 图片共 {len(slices)} 段，其中第 {', '.join(str(s) for s in failed_slices)} 段提取失败，内容可能不完整]"
                result += warning
                logger.warning(f"[FileParser] OpenRouter partial extraction: {len(failed_slices)}/{len(slices)} slices failed for {p.name}")
            logger.info(f"[FileParser] OpenRouter vision total: {len(result)} chars from {p.name}")
            return result
        return None
    except ImportError:
        logger.warning("[FileParser] Pillow/httpx not installed for OpenRouter vision")
        if details is not None:
            details["reason_code"] = "dependency_missing"
        return None
    except Exception as e:
        logger.warning(f"[FileParser] OpenRouter vision error: {e}")
        if details is not None:
            details.update(reason_code="exception", error_type=e.__class__.__name__)
        return None


def _build_openrouter_vision_client(settings):
    api_key = getattr(settings, 'OPENROUTER_API_KEY', '') or getattr(settings, 'OPENROUTER_API_KEY_EXTRACTION', '')
    if not api_key:
        return None
    import httpx
    from openai import OpenAI

    timeout_seconds = max(1.0, float(getattr(settings, "DOCUMENT_OCR_REQUEST_TIMEOUT_SECONDS", 120.0) or 120.0))
    max_retries = max(0, int(getattr(settings, "DOCUMENT_OCR_MAX_RETRIES", 2) or 0))
    client_kwargs = {
        "api_key": api_key,
        "base_url": "https://openrouter.ai/api/v1",
        "timeout": timeout_seconds,
        "max_retries": max_retries,
    }
    proxy_url = getattr(settings, 'PROXY_URL', None)
    if proxy_url:
        client_kwargs["http_client"] = httpx.Client(proxy=proxy_url, timeout=timeout_seconds)
    return OpenAI(**client_kwargs)


def _response_reported_cost(response) -> float | None:
    usage = getattr(response, "usage", None)
    if isinstance(usage, dict):
        raw_cost = usage.get("cost")
    else:
        raw_cost = getattr(usage, "cost", None)
    try:
        return float(raw_cost) if raw_cost is not None else None
    except (TypeError, ValueError):
        return None


def _response_reasoning_tokens(response) -> int | None:
    usage = getattr(response, "usage", None)
    if isinstance(usage, dict):
        details = usage.get("completion_tokens_details")
    else:
        details = getattr(usage, "completion_tokens_details", None)
    if isinstance(details, dict):
        raw_tokens = details.get("reasoning_tokens")
    else:
        raw_tokens = getattr(details, "reasoning_tokens", None)
    try:
        return max(0, int(raw_tokens)) if raw_tokens is not None else None
    except (TypeError, ValueError):
        return None


def _call_openrouter_vision_api(
    img,
    settings,
    vision_prompt: str = "",
    client=None,
    trace_collector: list[dict] | None = None,
    trace_context: dict | None = None,
    primary_model: str | None = None,
    fallback_model: str | None = None,
    primary_reasoning_effort: str | None = None,
    fallback_reasoning_effort: str | None = None,
) -> Optional[str]:
    """Send one PIL image to OpenRouter Vision and return extracted text."""
    try:
        import io, base64
        vision_model = primary_model or getattr(settings, 'PRECHECK_VISION_MODEL', 'google/gemini-3.1-flash-lite')
        resolved_fallback_model = (
            fallback_model
            if fallback_model is not None
            else getattr(settings, 'PRECHECK_VISION_FALLBACK', '')
        )
        vision_models = list(dict.fromkeys(model for model in [vision_model, resolved_fallback_model] if model))
        max_dim = 3000
        w, h = img.size
        if max(w, h) > max_dim:
            ratio = max_dim / max(w, h)
            img = img.resize((int(w * ratio), int(h * ratio)))

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        b64 = base64.b64encode(buf.getvalue()).decode()

        client = client or _build_openrouter_vision_client(settings)
        if client is None:
            return None

        last_error = None
        for attempt, model_name in enumerate(vision_models, start=1):
            started_at = time.perf_counter()
            try:
                configured_reasoning_effort = (
                    primary_reasoning_effort if attempt == 1 else fallback_reasoning_effort
                )
                if configured_reasoning_effort is None:
                    configured_reasoning_effort = getattr(
                        settings,
                        "DOCUMENT_OCR_REASONING_EFFORT"
                        if attempt == 1
                        else "DOCUMENT_OCR_FALLBACK_REASONING_EFFORT",
                        "none" if attempt == 1 else "minimal",
                    )
                reasoning_effort = str(
                    configured_reasoning_effort or ("none" if attempt == 1 else "minimal")
                ).strip().lower()
                resp = client.chat.completions.create(
                    model=model_name,
                    max_tokens=max(256, int(getattr(settings, "DOCUMENT_OCR_MAX_TOKENS", 8000) or 8000)),
                    temperature=0,
                    seed=0,
                    extra_body={
                        "reasoning": {
                            "effort": reasoning_effort,
                            "exclude": True,
                        }
                    },
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": vision_prompt or VISION_SYSTEM_PROMPT},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "auto"}},
                        ]
                    }]
                )
                choice = resp.choices[0]
                text = choice.message.content or ""
                finish_reason = str(getattr(choice, "finish_reason", None) or "") or None
                truncated = (finish_reason or "").lower() in {"length", "max_tokens"}
                trace = {
                    **(trace_context or {}),
                    "attempt": attempt,
                    "status": "truncated" if truncated else "success" if text.strip() else "empty",
                    "requested_model": model_name,
                    "actual_model": str(getattr(resp, "model", None) or model_name),
                    "provider": str(getattr(resp, "provider", None) or "") or None,
                    "reasoning_effort": reasoning_effort,
                    "duration_ms": int((time.perf_counter() - started_at) * 1000),
                }
                if finish_reason:
                    trace["finish_reason"] = finish_reason
                reported_cost = _response_reported_cost(resp)
                if reported_cost is not None:
                    trace["reported_cost_usd"] = reported_cost
                reasoning_tokens = _response_reasoning_tokens(resp)
                if reasoning_tokens is not None:
                    trace["reasoning_tokens"] = reasoning_tokens
                if trace_collector is not None:
                    trace_collector.append(trace)
                if text.strip() and not truncated:
                    return text.strip()
                if truncated:
                    logger.warning(
                        f"[FileParser] OpenRouter vision output truncated via {model_name}; "
                        "trying the configured fallback"
                    )
            except Exception as e:
                last_error = e
                if trace_collector is not None:
                    trace_collector.append(
                        {
                            **(trace_context or {}),
                            "attempt": attempt,
                            "status": "error",
                            "requested_model": model_name,
                            "actual_model": None,
                            "provider": None,
                            "duration_ms": int((time.perf_counter() - started_at) * 1000),
                            "error_type": e.__class__.__name__,
                        }
                    )
                logger.warning(
                    f"[FileParser] OpenRouter vision single image failed via {model_name}: "
                    f"{e.__class__.__name__}"
                )
        if last_error:
            logger.warning(
                "[FileParser] OpenRouter vision single image exhausted fallbacks: "
                f"{last_error.__class__.__name__}"
            )
        return None
    except Exception as e:
        logger.warning(f"[FileParser] OpenRouter vision single image failed: {e.__class__.__name__}")
        return None


def _extract_image_vision(p: Path, progress_cb=None, details: dict | None = None) -> Optional[str]:
    """用 GPT-5 Vision 提取图片文本，长图自动分块后拼接。"""
    from app.config import settings
    has_vision_key = bool(
        getattr(settings, 'FUSION_API_KEY', '')
        or getattr(settings, 'COMMITTEE_API_KEY_B', '')
        or getattr(settings, 'OPENAI_API_KEY', '')
    )
    if not has_vision_key:
        if details is not None:
            details["reason_code"] = "credential_unavailable"
        return None
    try:
        from PIL import Image
        import io, base64
        img = Image.open(str(p)).convert("RGB")
        w, h = img.size

        # 长图分块
        if h > LONG_IMAGE_THRESHOLD:
            slices = _slice_image(img, SLICE_HEIGHT, SLICE_OVERLAP)
            logger.info(f"[FileParser] Long image ({h}px), split into {len(slices)} slices: {p.name}")
        else:
            slices = [img]

        # 并发处理各分块，提升长图处理速度
        from concurrent.futures import ThreadPoolExecutor, as_completed
        texts_with_idx = []
        failed_slices = []
        max_workers = max(1, min(len(slices), int(getattr(settings, "DOCUMENT_OCR_MAX_WORKERS", 3) or 3)))
        completed = 0
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_call_vision_api, s, settings): i for i, s in enumerate(slices)}
            for fut in as_completed(futures):
                i = futures[fut]
                try:
                    text = fut.result()
                    if text:
                        texts_with_idx.append((i, text.strip()))
                        logger.debug(f"[FileParser] Slice {i+1}/{len(slices)} extracted {len(text)} chars")
                    else:
                        failed_slices.append(i + 1)
                        logger.warning(f"[FileParser] Slice {i+1}/{len(slices)} returned empty")
                except Exception as e:
                    failed_slices.append(i + 1)
                    logger.warning(f"[FileParser] Slice {i+1} failed: {e}")
                completed += 1
                if progress_cb:
                    progress_cb(completed, len(slices))

        failed_slices.sort()
        if details is not None:
            details.update(
                {
                    "width": w,
                    "height": h,
                    "slices": len(slices),
                    "completed_slices": len(texts_with_idx),
                    "failed_slices": failed_slices,
                    "max_workers": max_workers,
                }
            )

        if not texts_with_idx:
            return None

        # 按原始顺序拼接
        texts_with_idx.sort(key=lambda x: x[0])
        ordered_texts = [text for _, text in texts_with_idx]
        result = (
            dedup_adjacent_ocr_texts(ordered_texts)
            if len(ordered_texts) > 1
            else _strip_ocr_preamble(ordered_texts[0])
        )
        if failed_slices:
            warning = f"\n\n[⚠️ 图片共 {len(slices)} 段，其中第 {', '.join(str(s) for s in sorted(failed_slices))} 段提取失败，内容可能不完整]"
            result += warning
            logger.warning(f"[FileParser] Partial extraction: {len(failed_slices)}/{len(slices)} slices failed for {p.name}")
        logger.info(f"[FileParser] Vision extracted {len(result)} chars from {p.name} ({len(texts_with_idx)}/{len(slices)} slices OK)")
        return result

    except ImportError:
        logger.warning("[FileParser] Pillow not installed, vision extraction unavailable")
        if details is not None:
            details["reason_code"] = "dependency_missing"
        return None
    except Exception as e:
        logger.warning(f"[FileParser] Vision extraction error: {e}")
        if details is not None:
            details.update(reason_code="exception", error_type=e.__class__.__name__)
        return None


def _slice_image(img, slice_height: int, overlap: int) -> List:
    """将长图按高度切割为多个分块，相邻块有重叠避免内容截断。"""
    from PIL import Image
    w, h = img.size
    slices = []
    y = 0
    while y < h:
        bottom = min(y + slice_height, h)
        slices.append(img.crop((0, y, w, bottom)))
        if bottom >= h:
            break
        y += slice_height - overlap  # 保留 overlap 像素重叠
    return slices


def _call_vision_api(img, settings) -> Optional[str]:
    """将单张 PIL 图片送给视觉大模型，返回提取的文字。"""
    import io, base64
    import httpx
    from openai import OpenAI

    # 优先用有 gpt-5 权限的 Key（FUSION > COMMITTEE_B > OPENAI 主Key）
    api_key = (
        getattr(settings, 'FUSION_API_KEY', '')
        or getattr(settings, 'COMMITTEE_API_KEY_B', '')
        or getattr(settings, 'OPENAI_API_KEY', '')
    )
    if not api_key:
        logger.warning("[FileParser] No Vision API key available")
        return None

    # 编码为 base64 JPEG（压缩到合理大小）
    buf = io.BytesIO()
    # 限制图片尺寸避免 base64 过大
    max_dim = 3000
    w, h = img.size
    if max(w, h) > max_dim:
        ratio = max_dim / max(w, h)
        img = img.resize((int(w * ratio), int(h * ratio)))
    img.save(buf, format="JPEG", quality=90)
    b64 = base64.b64encode(buf.getvalue()).decode()

    kwargs = {
        "api_key": api_key,
        "http_client": httpx.Client(proxy=None),  # 不走系统代理
    }
    if settings.OPENAI_BASE_URL:
        kwargs["base_url"] = settings.OPENAI_BASE_URL

    client = OpenAI(**kwargs)
    vision_model = "gpt-5" if settings.OPENAI_BASE_URL else "gpt-4o"

    try:
        resp = client.chat.completions.create(
            model=vision_model,
            max_tokens=2000,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": VISION_SYSTEM_PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "auto"}},
                ]
            }]
        )
        return resp.choices[0].message.content
    except Exception as e:
        logger.warning(f"[FileParser] Vision API call failed: {e}")
        return None


def _extract_image_ocr(p: Path, progress_cb=None, details: dict | None = None) -> Optional[str]:
    """降级方案：使用 pytesseract OCR 提取图片文本（精度有限）。"""
    try:
        import shutil
        import pytesseract
        from PIL import Image
        from app.config import settings

        tesseract_cmd = str(getattr(pytesseract.pytesseract, "tesseract_cmd", "tesseract") or "tesseract")
        if not Path(tesseract_cmd).is_file() and shutil.which(tesseract_cmd) is None:
            logger.warning("[FileParser] pytesseract binding is installed but Tesseract runtime is unavailable")
            if details is not None:
                details["reason_code"] = "runtime_missing"
            return None

        img = Image.open(str(p)).convert("RGB")
        w, h = img.size
        slices = _slice_image(img, SLICE_HEIGHT, SLICE_OVERLAP) if h > LONG_IMAGE_THRESHOLD else [img]
        from concurrent.futures import ThreadPoolExecutor, as_completed

        max_workers = max(1, min(len(slices), int(getattr(settings, "DOCUMENT_OCR_MAX_WORKERS", 3) or 3)))
        texts_with_idx = []
        failed_slices = []
        completed = 0
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(pytesseract.image_to_string, image.convert("L"), lang="chi_sim+eng"): idx
                for idx, image in enumerate(slices)
            }
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    text = (future.result() or "").strip()
                except Exception as exc:
                    text = ""
                    logger.warning(f"[FileParser] Tesseract slice {idx+1} failed: {exc}")
                if text:
                    texts_with_idx.append((idx, text))
                else:
                    failed_slices.append(idx + 1)
                completed += 1
                if progress_cb:
                    progress_cb(completed, len(slices))

        texts_with_idx.sort(key=lambda item: item[0])
        failed_slices.sort()
        if details is not None:
            details.update(
                {
                    "width": w,
                    "height": h,
                    "slices": len(slices),
                    "completed_slices": len(texts_with_idx),
                    "failed_slices": failed_slices,
                    "max_workers": max_workers,
                }
            )
        if not texts_with_idx:
            return None
        ordered_texts = [text for _, text in texts_with_idx]
        result = dedup_adjacent_ocr_texts(ordered_texts) if len(ordered_texts) > 1 else ordered_texts[0]
        if failed_slices:
            result += (
                f"\n\n[⚠️ 图片共 {len(slices)} 段，其中第 "
                f"{', '.join(str(value) for value in failed_slices)} 段提取失败，内容可能不完整]"
            )
        logger.info(f"[FileParser] OCR fallback extracted {len(result)} chars from {p.name}")
        return result
    except ImportError:
        logger.warning("[FileParser] pytesseract / Pillow not installed, image OCR unavailable")
        if details is not None:
            details["reason_code"] = "dependency_missing"
        return None
    except Exception as e:
        logger.warning(f"[FileParser] Image OCR error: {e}")
        if details is not None:
            details.update(reason_code="exception", error_type=e.__class__.__name__)
        return None


def count_words(text: str) -> int:
    """估算中英混合文本的词数"""
    if not text:
        return 0
    import re
    # 中文字符数 + 英文单词数
    chinese = len(re.findall(r'[\u4e00-\u9fff]', text))
    english = len(re.findall(r'[a-zA-Z]+', text))
    return chinese + english


def extract_chat_time_range(text: str) -> Optional[dict]:
    """从聊天记录中识别对话时间范围。

    返回 {"earliest": "YYYY-MM-DD HH:MM", "latest": "YYYY-MM-DD HH:MM", "message_count": int}
    未找到时间戳则返回 None。
    """
    import re
    from datetime import datetime as _dt

    # 常见聊天记录时间戳模式
    patterns = [
        # 2024-03-15 14:30:22 / 2024/03/15 14:30
        r'(\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?)',
        # 03/15/2024 14:30
        r'(\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2})',
        # 2024年3月15日 14:30
        r'(\d{4}年\d{1,2}月\d{1,2}日\s*\d{1,2}:\d{2})',
        # [14:30:22] / [2024-03-15 14:30]
        r'\[(\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?)\]',
        # 下午 3:30 / 上午 10:15（微信格式）
        r'(\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+[上下]午\s*\d{1,2}:\d{2})',
    ]

    timestamps: list[str] = []
    for pat in patterns:
        matches = re.findall(pat, text)
        timestamps.extend(matches)

    if not timestamps:
        return None

    # 尝试解析为 datetime 并找出范围
    parsed: list[_dt] = []
    for ts in timestamps:
        ts_clean = ts.replace('年', '-').replace('月', '-').replace('日', '').strip()
        # 处理上午/下午
        ts_clean = re.sub(r'上午\s*', '', ts_clean)
        ts_clean = re.sub(r'下午\s*(\d+)', lambda m: str(int(m.group(1)) + 12), ts_clean)
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S",
                     "%Y/%m/%d %H:%M", "%m/%d/%Y %H:%M"):
            try:
                parsed.append(_dt.strptime(ts_clean, fmt))
                break
            except ValueError:
                continue

    if not parsed:
        return None

    earliest = min(parsed)
    latest = max(parsed)
    return {
        "earliest": earliest.strftime("%Y-%m-%d %H:%M"),
        "latest": latest.strftime("%Y-%m-%d %H:%M"),
        "message_count": len(parsed),
        "duration_minutes": int((latest - earliest).total_seconds() / 60),
    }


def extract_date_from_text(text: str) -> Optional[str]:
    """用 LLM 从文本中识别文章的发布/撰写日期，返回 'YYYY-MM-DD' 格式字符串，未找到返回 None。

    使用 LLM 而非正则，避免将文章内引用的历史日期错判为文章本身的日期。
    只取文本前 1500 字符，日期通常出现在开头。
    """
    if not text or len(text.strip()) < 10:
        return None

    snippet = text[:1500].strip()

    system_prompt = (
        "你是文档元数据提取助手。\n"
        "任务：从给定文本片段中识别该文章/文档本身的发布日期或撰写日期。\n"
        "\n规则：\n"
        "1. 只识别文章自身的日期（如页眉日期、文章发布时间、报告日期），\n"
        "   不要返回文章内容中引用的历史事件日期或数据日期。\n"
        "2. 若找到日期，严格按 YYYY-MM-DD 格式输出，仅输出该日期字符串，不附加任何说明。\n"
        "3. 若无法确定文章自身的日期，输出 null（不要猜测）。\n"
        "4. 不输出任何解释、标点或换行之外的内容。"
    )
    user_prompt = f"以下是文章开头内容，请提取文章自身的发布日期：\n\n{snippet}"

    try:
        from app.utils.llm_client import llm_client
        response = llm_client.complete(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            skill_role="extraction",
            max_tokens=20,
            temperature=0.0,
        )
        if not response:
            return None
        result = response.strip().strip('"').strip("'").strip()
        if result.lower() in ("null", "none", "", "无", "未知"):
            return None
        # 简单格式校验：必须是 YYYY-MM-DD
        import re
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', result):
            return result
        # 容错：LLM 可能返回 2024/03/15 或 2024年3月15日
        m = re.search(r'(\d{4})[^\d](\d{1,2})[^\d](\d{1,2})', result)
        if m:
            y, mo, d = m.group(1), m.group(2).zfill(2), m.group(3).zfill(2)
            return f"{y}-{mo}-{d}"
        return None
    except Exception as e:
        logger.debug(f"[FileParser] LLM date extraction failed: {e}")
        return None


# ============================================================
# 多截图重叠去重
# ============================================================

# OCR 常见前言行 — 会被自动删除
_OCR_PREAMBLE_PATTERNS = [
    re.compile(r'^以下是.*(?:完整)?转录[：:]?\s*$'),
    re.compile(r'^根据您提供的图片.*[：:]?\s*$'),
    re.compile(r'^以下是图片.*内容.*[：:]?\s*$'),
    re.compile(r'^The following is.*transcri(?:pt|ption).*[:]?\s*$', re.IGNORECASE),
]


def _normalize_for_dedup(line: str) -> str:
    """归一化行文本用于模糊比对：去 markdown、折叠空白、去标点差异。"""
    s = line.strip()
    s = re.sub(r'\*{1,2}', '', s)           # 去 markdown bold
    s = re.sub(r'\s+', ' ', s)              # 折叠空白
    s = s.replace('：', ':').replace('，', ',').replace('。', '.')  # 统一标点
    return s


def _strip_ocr_preamble(text: str) -> str:
    """去除 OCR 输出头部的常见前言行。"""
    lines = text.split('\n')
    start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        is_preamble = any(p.match(stripped) for p in _OCR_PREAMBLE_PATTERNS)
        if is_preamble:
            start = i + 1
            continue
        break  # 遇到第一个非空非前言行就停止
    if start > 0:
        text = '\n'.join(lines[start:])
        logger.debug(f"[FileParser] Stripped {start} preamble lines from OCR output")
    return text


def dedup_adjacent_ocr_texts(texts: list[str], min_overlap_lines: int = 2) -> str:
    """合并多段 OCR 文本时，去除相邻截图之间的重叠行。

    连续截屏时，前一张底部 ≈ 后一张顶部有重叠区域。
    通过归一化后的行比对（去 markdown/空白/标点差异），找到最长重叠来去重。
    同时自动去除 OCR 输出中的常见前言行。

    Args:
        texts: OCR 提取的文本列表（按截图顺序）
        min_overlap_lines: 最少连续匹配行数才算重叠（防误判）

    Returns:
        去重后的合并文本
    """
    if not texts:
        return ""

    # 预处理：去除每段的 OCR 前言
    cleaned = [_strip_ocr_preamble(t) for t in texts]
    cleaned = [t for t in cleaned if t.strip()]

    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]

    merged = cleaned[0]

    for i in range(1, len(cleaned)):
        # 用归一化后的行做比对
        prev_raw = [l for l in merged.split('\n') if l.strip()]
        curr_raw = [l for l in cleaned[i].split('\n') if l.strip()]
        prev_norm = [_normalize_for_dedup(l) for l in prev_raw]
        curr_norm = [_normalize_for_dedup(l) for l in curr_raw]

        if not prev_norm or not curr_norm:
            merged = merged + "\n\n" + cleaned[i]
            continue

        # 找最长重叠：prev 尾部 N 行 ≈ curr 头部 N 行（归一化比对）
        best_overlap = 0
        max_check = min(len(prev_norm), len(curr_norm), 25)
        for overlap_len in range(max_check, min_overlap_lines - 1, -1):
            tail = prev_norm[-overlap_len:]
            head = curr_norm[:overlap_len]
            if tail == head:
                best_overlap = overlap_len
                break

        if best_overlap >= min_overlap_lines:
            # 从 curr 原始文本中跳过前 best_overlap 个非空行
            raw_lines = cleaned[i].split('\n')
            non_empty_count = 0
            cut_index = 0
            for j, line in enumerate(raw_lines):
                if line.strip():
                    non_empty_count += 1
                    if non_empty_count > best_overlap:
                        cut_index = j
                        break
            else:
                cut_index = len(raw_lines)

            remaining = '\n'.join(raw_lines[cut_index:]).strip()
            if remaining:
                merged = merged + "\n\n" + remaining
            logger.info(f"[FileParser] Dedup: removed {best_overlap} overlapping lines between text {i} and {i+1}")
        else:
            merged = merged + "\n\n" + cleaned[i]

    return merged


# ============================================================
# 微信聊天结构化解析 & 图片分类
# ============================================================

def parse_wechat_chat(text: str) -> Optional[dict]:
    """解析 OCR 提取的微信聊天记录文本，返回结构化数据。

    返回格式：
    {
        "is_chat": True,
        "speakers": {"发言人A": 15, "发言人B": 3, ...},  # 发言次数
        "main_speaker": "发言人A",  # 消息最多的人
        "messages": [{"speaker": "...", "time": "...", "content": "..."}, ...],
        "image_positions": [3, 7],  # 第几条消息是 [图片] 占位
        "total_messages": 20,
        "time_range": {"earliest": "...", "latest": "..."},
    }
    未识别到聊天格式则返回 None。
    """
    import re

    if not text or len(text.strip()) < 30:
        return None

    # ── 发言人名字符类（尽可能宽泛，覆盖各种昵称） ──
    # 支持中文、字母、数字、空格、括号、@、/、|、emoji相关标点等
    _NAME = r'[\u4e00-\u9fff\w@/|][\u4e00-\u9fff\w\s\(\)（）~·\-\.@/|&\'\"]{0,35}?'

    # ── 时间戳模板（模板化，每种格式独立，便于扩展） ──
    # 每个 tuple = (完整行头正则, group1=speaker, group2=timestamp)
    # 所有正则都要求 发言人 与 时间 之间有 ≥2 空格
    _TS_PATTERNS = [
        # P1: "发言人  2026年4月15日 17:00" / "发言人  2026年4月15日 17:00:30"
        r'\d{4}年\d{1,2}月\d{1,2}日\s*\d{1,2}:\d{2}(?::\d{2})?',
        # P2: "发言人  2026/4/15 17:00" / "发言人  2026-04-15 17:00"
        r'\d{4}[/\-]\d{1,2}[/\-]\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?',
        # P3: "发言人  04-15 16:31:56" / "发言人  4/15 16:31"
        r'\d{1,2}[/\-]\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?',
        # P4: "发言人  昨天 16:31" / "发言人  星期二 16:31" / "发言人  周三 16:31"
        r'(?:今天|昨天|前天|星期[一二三四五六日天]|周[一二三四五六日天])\s+\d{1,2}:\d{2}(?::\d{2})?',
        # P5: "发言人  HH:MM:SS" / "发言人  H:MM AM/PM" (纯时间，最宽松，放最后)
        r'\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AaPp][Mm])?',
        # P6: English date "发言人  Apr 15, 2026 5:00 PM" (iOS英文微信)
        r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},?\s+\d{4}\s+\d{1,2}:\d{2}(?:\s*[AaPp][Mm])?',
    ]

    # 组装完整行头正则列表
    patterns = []
    for ts in _TS_PATTERNS:
        # 允许发言人名前后有 markdown bold (**)
        patterns.append(rf'^(?:\*{{0,2}})({_NAME})(?:\*{{0,2}})\s{{2,}}({ts})')

    # 额外模式：方括号包裹的时间戳（第三方导出工具格式）
    # "[2026-04-15 17:00:30] 发言人: 内容" 或 "[17:00] 发言人: 内容"
    bracket_pattern = r'^\[(\d{4}[/\-]\d{1,2}[/\-]\d{1,2}\s+)?\d{1,2}:\d{2}(?::\d{2})?\]\s*'

    messages = []
    speaker_counts: dict[str, int] = {}
    image_positions = []
    timestamps = []

    lines = text.split("\n")
    current_speaker = None
    current_time = None
    current_content_lines: list[str] = []
    matched_count = 0

    def _flush():
        nonlocal current_speaker, current_time, current_content_lines
        if current_speaker and current_content_lines:
            content = "\n".join(current_content_lines).strip()
            msg = {"speaker": current_speaker, "time": current_time or "", "content": content}
            messages.append(msg)
            speaker_counts[current_speaker] = speaker_counts.get(current_speaker, 0) + 1
            if current_time:
                timestamps.append(current_time)
            # 检测图片占位
            if "[图片]" in content or content.strip() == "[图片]":
                image_positions.append(len(messages) - 1)
            current_content_lines = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        matched = False
        # 标准格式：发言人  时间戳
        for pat in patterns:
            m = re.match(pat, stripped)
            if m:
                _flush()
                current_speaker = m.group(1).strip().strip("*")
                current_time = m.group(2).strip()
                matched = True
                matched_count += 1
                break

        # 第三方导出格式：[时间戳] 发言人: 内容
        if not matched:
            bm = re.match(bracket_pattern, stripped)
            if bm:
                rest = stripped[bm.end():]
                # 尝试提取 "发言人: 内容" 或 "发言人：内容"
                name_m = re.match(r'^([^:：]{1,30})[:：]\s*', rest)
                if name_m:
                    _flush()
                    current_speaker = name_m.group(1).strip()
                    current_time = stripped[1:bm.end()-1].strip().rstrip(']').strip()
                    current_content_lines.append(rest[name_m.end():].strip())
                    matched = True
                    matched_count += 1

        if not matched:
            if current_speaker:
                current_content_lines.append(stripped)
            # 未匹配且无 current_speaker 的行：可能是标题行（"群聊的聊天记录"）

    _flush()  # 最后一条

    # 判断是否为聊天格式：至少匹配到 2 条消息头（支持短对话）
    if matched_count < 2:
        return None

    # 找出主要发言人（消息最多的人）
    main_speaker = max(speaker_counts, key=speaker_counts.get) if speaker_counts else None

    # 时间范围
    time_range = None
    if timestamps:
        time_range = {"earliest": timestamps[0], "latest": timestamps[-1]}

    return {
        "is_chat": True,
        "speakers": speaker_counts,
        "main_speaker": main_speaker,
        "messages": messages,
        "image_positions": image_positions,
        "total_messages": len(messages),
        "time_range": time_range,
    }


def classify_image_in_batch(file_path: str, all_files_in_batch: list = None) -> str:
    """根据文件名模式推断图片类型，适用于各种来源和设备。

    支持的命名模式：
    - Windows 截屏: "屏幕截图 2026-04-16 013618.png"
    - macOS 截屏: "截屏2026-04-16 01.36.18.png" / "Screenshot 2026-04-16 at 1.36.18.png"
    - iOS 截屏: "IMG_1234.PNG" (纯数字序列)
    - Android 截屏: "Screenshot_20260416_013618.png" / "Screenshot_20260416-013618.jpg"
    - 微信保存图片: "微信图片_20260416013955.jpg"
    - 微信内容图: "微信图片_20260416_013847_354.jpg" (带额外后缀)
    - 第三方截图工具: "CleanShot*" / "Snipaste*" / "Monosnap*"
    - 微信/QQ hash 文件名: "47b465e44b4980f47664355a9cf648f4.jpg"

    返回：
    - "chat_screenshot" : 聊天截图 / 屏幕截图
    - "content_image"   : 聊天中发送的内容图（非截图）
    - "article_screenshot" : 文章/公众号截图
    - "unknown"         : 无法判断
    """
    from pathlib import Path
    import re

    name = Path(file_path).name
    name_lower = name.lower()
    stem = Path(file_path).stem.lower()

    # ── 规则 1: 各平台截图命名 ──
    screenshot_indicators = [
        "屏幕截图",      # Windows 中文
        "截屏",          # macOS 中文
        "screenshot",    # Windows/macOS/Android 英文
        "cleanshot",     # macOS CleanShot
        "snipaste",      # Snipaste
        "monosnap",      # Monosnap
        "greenshot",     # Greenshot
    ]
    if any(ind in name_lower for ind in screenshot_indicators):
        return "chat_screenshot"

    # ── 规则 2: 微信保存图片 ──
    if "微信图片" in name or "wechat" in name_lower:
        # "微信图片_YYYYMMDD_HHMMSS_NNN.jpg" — 带额外数字后缀 = 内容图（聊天中转发的）
        # "微信图片_YYYYMMDDHHMMSS.jpg" — 标准格式 = 聊天截图
        if re.search(r'微信图片_\d{8}_\d{6}_\d+', name):
            # 如果同批次有截屏类图片，这类更可能是内容图
            if all_files_in_batch:
                has_screenshots = any(
                    any(ind in Path(f).name.lower() for ind in screenshot_indicators)
                    for f in all_files_in_batch if str(f) != str(file_path)
                )
                if has_screenshots:
                    return "content_image"
        # 标准微信截图格式
        return "chat_screenshot"

    # ── 规则 3: iOS IMG_XXXX.PNG/JPG ──
    if re.fullmatch(r'img_\d{4,6}', stem):
        # 如果同批次有多个 IMG_ 序列 = 多张截屏
        return "chat_screenshot"

    # ── 规则 4: Android Screenshot_YYYYMMDD_HHMMSS ──
    if re.fullmatch(r'screenshot_\d{8}[_\-]\d{6}', stem):
        return "chat_screenshot"

    # ── 规则 5: hash 文件名 (微信/QQ 保存的图) ──
    if re.fullmatch(r'[0-9a-f]{16,40}', stem):
        return "chat_screenshot"

    # ── 规则 6: QQ截图格式 "QQ图片YYYYMMDDHHMMSS" / "QQ截图*" ──
    if "qq截图" in name_lower or "qq图片" in name_lower:
        return "chat_screenshot"

    # ── 规则 7: Telegram 导出格式 "photo_YYYY-MM-DD_HH-MM-SS" ──
    if re.fullmatch(r'photo_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}', stem):
        return "chat_screenshot"

    # ── 规则 8: 如果同批次有截屏类图片，非截屏的图可能是内容图 ──
    if all_files_in_batch:
        other_names = [Path(f).name.lower() for f in all_files_in_batch if str(f) != str(file_path)]
        batch_has_screenshots = any(
            any(ind in n for ind in screenshot_indicators)
            or re.fullmatch(r'[0-9a-f]{16,40}\.[a-z]+', n)
            for n in other_names
        )
        if batch_has_screenshots:
            return "content_image"

    return "unknown"


def match_content_images_to_chat(
    chat_messages: list,
    content_image_texts: dict,
    content_image_files: list,
) -> list:
    """将内容图片的 OCR 文本插入到聊天记录的 [图片] 占位位置。

    Args:
        chat_messages: parse_wechat_chat 返回的 messages 列表
        content_image_texts: {文件名: OCR文本} 映射
        content_image_files: 内容图文件路径列表（按文件名排序）

    Returns:
        更新后的 messages 列表（[图片] 被替换为实际内容）
    """
    if not content_image_texts or not chat_messages:
        return chat_messages

    # 找出所有 [图片] 占位的消息索引
    image_slots = []
    for i, msg in enumerate(chat_messages):
        if "[图片]" in msg["content"]:
            image_slots.append(i)

    # 按顺序匹配：第 N 个 [图片] 对应第 N 个内容图
    sorted_content = sorted(content_image_files)
    for slot_idx, img_file in zip(image_slots, sorted_content):
        fname = Path(img_file).name
        img_text = content_image_texts.get(fname, "")
        if img_text:
            old_content = chat_messages[slot_idx]["content"]
            replacement = f"[图片内容：{img_text.strip()}]"
            chat_messages[slot_idx]["content"] = old_content.replace("[图片]", replacement, 1)
            logger.info(f"[FileParser] Matched content image '{fname}' to chat position {slot_idx}")

    return chat_messages


def rebuild_chat_text(parsed: dict) -> str:
    """将 parse_wechat_chat 的结构化结果重建为格式化文本。"""
    lines = []
    for msg in parsed.get("messages", []):
        header = msg["speaker"]
        if msg.get("time"):
            header += f"  {msg['time']}"
        lines.append(header)
        lines.append(msg["content"])
        lines.append("")  # 空行分隔
    return "\n".join(lines).strip()


def extract_speaker_from_header(text: str) -> Optional[str]:
    """从文本开头检测是否有微信消息头格式（发言人 + 时间戳），提取发言人名。

    适用于单人长文（chat parser 因消息数不足而不触发时的补充）。
    例如：
    - "Silence  04-15 16:31:56" → "Silence"
    - "大卫李  2026年4月14日 16:11" → "大卫李"
    - "< Street Alpha  ..." → "Street Alpha" （公众号文章）
    """
    import re
    first_line = text.strip().split("\n")[0].strip()

    # 去除可能的 markdown bold
    first_line = first_line.strip("*")

    # 移除微信聊天记录中的 "< " 前缀（返回按钮标记）
    first_line = re.sub(r'^<\s*', '', first_line).strip()

    # 发言人名字符类（与 parse_wechat_chat 保持一致）
    _N = r'[\u4e00-\u9fff\w@/|][\u4e00-\u9fff\w\s\(\)（）~·\-\.@/|&\'\"]{0,35}?'
    # 模式：名字 + 2个以上空格 + 时间戳
    patterns = [
        rf'^({_N})\s{{2,}}\d{{4}}年\d{{1,2}}月\d{{1,2}}日',
        rf'^({_N})\s{{2,}}\d{{4}}[/\-]\d{{1,2}}[/\-]\d{{1,2}}\s+\d{{1,2}}:\d{{2}}',
        rf'^({_N})\s{{2,}}\d{{1,2}}[/\-]\d{{1,2}}\s+\d{{1,2}}:\d{{2}}',
        rf'^({_N})\s{{2,}}(?:今天|昨天|前天|星期[一二三四五六日天]|周[一二三四五六日天])\s+\d{{1,2}}:\d{{2}}',
        rf'^({_N})\s{{2,}}\d{{1,2}}:\d{{2}}',
        rf'^({_N})\s{{2,}}(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{{1,2}}',
    ]

    for pat in patterns:
        m = re.match(pat, first_line)
        if m:
            name = m.group(1).strip()
            # 排除太短或太长的结果
            if 1 <= len(name) <= 25:
                return name
    return None


def infer_source_type_from_text(text: str) -> str:
    """根据 OCR 文本内容推断 source_type。

    返回: "chat" | "article" | "image"
    """
    if not text:
        return "image"

    parsed = parse_wechat_chat(text)
    if parsed and parsed.get("is_chat"):
        return "chat"

    # 检测文章特征：需要多个信号共同判定，避免聊天中的链接标题误判
    import re
    first_1000 = text[:1000]
    article_signals = [
        bool(re.search(r'^#+\s', first_1000, re.MULTILINE)),  # Markdown 标题
        bool(re.search(r'(作者|来源|编辑|原创|声明|摘要|导读)[:：]', first_1000)),
        bool(re.search(r'\d+/\d+', first_1000)),  # 分页 1/7
        len(text) > 800,  # 文章通常较长
        len(text.split('\n\n')) > 3,  # 多个段落
    ]
    if sum(article_signals) >= 2:
        return "article"

    return "image"
