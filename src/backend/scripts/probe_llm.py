from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import load_settings  # noqa: E402
from app.services.llm import DEFAULT_OUTPUT_TOKENS, LLMClient, budget_for  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]


async def _timed(client: LLMClient, label: str, model: str, content, max_tokens: int) -> dict:
    started = time.monotonic()
    resp = await client.client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": content}],
        temperature=0,
        max_tokens=max_tokens,
    )
    elapsed = round(time.monotonic() - started, 1)
    choice = resp.choices[0]
    usage = resp.usage
    row = {
        "case": label,
        "model": model,
        "max_tokens": max_tokens,
        "seconds": elapsed,
        "finish_reason": choice.finish_reason,
        "completion_tokens": usage.completion_tokens if usage else None,
        "reasoning_chars": len(getattr(choice.message, "reasoning_content", None) or ""),
        "content_chars": len(choice.message.content or ""),
    }
    print(json.dumps(row, ensure_ascii=False))
    return row


async def main() -> None:
    settings = load_settings()
    client = LLMClient(settings.llm)
    print(json.dumps({"base_url": settings.llm.base_url, "text_model": settings.llm.text_model,
                      "vision_model": settings.llm.vision_model}, ensure_ascii=False))

    capability = await client.probe()
    print(json.dumps({"capability": capability}, ensure_ascii=False))

    rows = []
    rows.append(await _timed(client, "tiny", settings.llm.text_model, "Trả lời đúng một từ: OK", 64))
    rows.append(
        await _timed(
            client,
            "instructed-json",
            settings.llm.text_model,
            "Trả JSON {\"ok\": true}. Trả lời trực tiếp, không suy luận.",
            512,
        )
    )

    from app.services.fields import EXTRACTABLE_FIELDS, fields_for
    from app.services.ingest import parse_document, render_pdf_page_png
    from app.services.schemas import EXTRACT_PROMPT, render_prompt

    bill = REPO_ROOT / "data" / "sample" / "input" / "bill.pdf"
    parsed = parse_document(bill)
    prompt = render_prompt(
        EXTRACT_PROMPT, doc_type="TRANSPORT_DOCUMENT", filename=bill.name, content=parsed["pages"][0]
    )
    requested = budget_for(len(fields_for("TRANSPORT_DOCUMENT")), False)
    print(json.dumps({"extraction_budget_requested": requested, "configured_max": settings.llm.max_tokens,
                      "effective": min(requested, settings.llm.max_tokens)}, ensure_ascii=False))
    rows.append(await _timed(client, "extract-text", settings.llm.text_model, prompt, min(requested, settings.llm.max_tokens)))

    scan = REPO_ROOT / "data" / "sample" / "input" / "CONTRACT DALIAN CHUNLING LS2628.pdf"
    png = render_pdf_page_png(scan, 0, dpi=150)
    import base64

    vision_content = [
        {"type": "text", "text": render_prompt(EXTRACT_PROMPT, doc_type="SALES_CONTRACT_PO", filename=scan.name, content="(ảnh)")},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}},
    ]
    rows.append(await _timed(client, "extract-vision", settings.llm.vision_model, vision_content, DEFAULT_OUTPUT_TOKENS))

    out = REPO_ROOT / "data" / "reports" / "gateway_capabilities.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"capability": capability, "measurements": rows,
                    "default_output_tokens": DEFAULT_OUTPUT_TOKENS}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"wrote {out}")


if __name__ == "__main__":
    asyncio.run(main())
