"""Gradio 可视化界面: 自然语言控制 agent + 实时状态监控。

启动: python -m src.ui.app
访问: http://localhost:7860
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

import gradio as gr

from src.ui.intent_parser import parse_intent, format_intent_response
from src.ui.bridge import take_screenshot, get_memory_text, get_principles_text, list_stages, run_pipeline_stream

log = logging.getLogger(__name__)

CSS = """
#status { min-height: 400px; }
#output { min-height: 400px; }
#sidebar { min-height: 400px; }
"""

LAST_STAGE = {"stage": "1-7"}


def handle_input(text: str):
    """解析用户输入,返回回复 + 执行动作。"""
    intent = parse_intent(text)
    reply = format_intent_response(intent)
    return reply, intent


async def execute_intent(intent: dict):
    """执行解析后的意图,异步 yield 状态更新。"""
    action = intent.get("action", "unknown")

    if action == "unknown":
        yield "无法理解指令"
        return

    if action == "show_memory":
        yield get_memory_text()
        return

    if action == "show_principles":
        yield get_principles_text()
        return

    if action == "screenshot":
        yield "截图中..."
        path = take_screenshot()
        if path:
            yield path
        else:
            yield "截图失败 (模拟器未连接?)"
        return

    if action == "list_stages":
        yield list_stages()
        return

    if action == "rerun":
        stage = LAST_STAGE.get("stage", "1-7")
        intent = {"action": "run", "stage": stage, "fresh": False}

    if action == "run":
        stage = intent.get("stage", "1-7")
        LAST_STAGE["stage"] = stage
        fresh = True
        preferred = intent.get("preferred_opers")

        status_lines = []
        async for event in run_pipeline_stream(stage, fresh, preferred):
            detail = event.get("detail", "")
            st = event.get("stage", "")
            status_val = event.get("status", "")

            if st == "error":
                status_lines.append("ERROR: {}".format(detail))
                yield "\n".join(status_lines)
                return

            status_lines.append("[{}] {}".format(st, detail))

            # 如果有作业数据,显示 JSON
            if st == "job" and event.get("data"):
                job = event["data"]
                opers = job.get("opers", [])
                actions = job.get("actions", [])
                status_lines.append("\n=== 作业 ===")
                status_lines.append("干员: {}".format(", ".join(
                    "{}(skill{})".format(o.get("name", "?"), o.get("skill", 1)) for o in opers)))
                status_lines.append("动作: {} 个".format(len(actions)))
                for a in actions:
                    status_lines.append("  {}".format(json.dumps(a, ensure_ascii=False)[:80]))

            yield "\n".join(status_lines)

        return

    yield "暂不支持此操作"


def screenshot_handler():
    """截图按钮。"""
    path = take_screenshot()
    if path:
        return path, "截图成功"
    return None, "截图失败 (模拟器未连接?)"


def memory_handler():
    """记忆按钮。"""
    return get_memory_text()


def principles_handler():
    """原则按钮。"""
    return get_principles_text()


def stages_handler():
    """关卡列表按钮。"""
    return list_stages()


def build_app() -> gr.Blocks:
    """构建 Gradio 界面。"""
    with gr.Blocks(title="明日方舟 AI Agent") as app:
        gr.Markdown("# 明日方舟 AI Agent 控制台")

        with gr.Row():
            # 输入区
            with gr.Column(scale=4):
                chat_input = gr.Textbox(
                    label="输入指令",
                    placeholder="打1-7 / 用维什戴尔打CE-5 / 换山为煌 / 再打一次 / 显示记忆 / 截图",
                    lines=1,
                )
                run_btn = gr.Button("执行", variant="primary")
                reply_box = gr.Textbox(label="解析结果", interactive=False, lines=1)

            # 快捷按钮
            with gr.Column(scale=1):
                screenshot_btn = gr.Button("截图")
                memory_btn = gr.Button("记忆")
                principles_btn = gr.Button("原则")
                stages_btn = gr.Button("关卡列表")

        with gr.Row():
            # 主输出区
            with gr.Column(scale=3):
                output_text = gr.Textbox(
                    label="Agent 输出",
                    interactive=False,
                    lines=25,
                    elem_id="output",
                )
                screenshot_img = gr.Image(label="模拟器画面", type="filepath")

            # 侧边栏
            with gr.Column(scale=2, elem_id="sidebar"):
                sidebar_text = gr.Textbox(
                    label="信息面板",
                    interactive=False,
                    lines=25,
                    value="点击上方按钮查看记忆/原则/关卡列表",
                )

        # 事件绑定
        async def on_run(text):
            """异步流式执行,Gradio 原生支持 async generator。"""
            intent = parse_intent(text)
            reply = format_intent_response(intent)
            yield reply, ""

            if intent.get("action") not in ("run", "rerun"):
                async for chunk in execute_intent(intent):
                    yield reply, chunk
                return

            async for chunk in execute_intent(intent):
                yield reply, chunk

        run_btn.click(
            on_run,
            inputs=[chat_input],
            outputs=[reply_box, output_text],
        )
        chat_input.submit(
            on_run,
            inputs=[chat_input],
            outputs=[reply_box, output_text],
        )

        screenshot_btn.click(
            screenshot_handler,
            outputs=[screenshot_img, sidebar_text],
        )
        memory_btn.click(memory_handler, outputs=[sidebar_text])
        principles_btn.click(principles_handler, outputs=[sidebar_text])
        stages_btn.click(stages_handler, outputs=[sidebar_text])

    return app


def main():
    """启动 Gradio 服务器。"""
    app = build_app()
    app.launch(
        server_name="127.0.0.1",
        server_port=7860,
        share=False,
        show_error=True,
        css=CSS,
        theme=gr.themes.Soft(),
    )


if __name__ == "__main__":
    main()
