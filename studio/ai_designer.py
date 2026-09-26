"""Convert model-directed frame sequences or scrolling canvases to DP104 HSV."""

from __future__ import annotations

import colorsys
import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import requests

from .effects import COLS, ROWS, MAX_FRAMES

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"

SPEC = """你是专业的 8×24 实体 LED 像素动画师。作品会在键盘的 8 行、24 列共 192 颗灯珠上循环播放；x=0..23、y=0..7。JSON 网格中每个字符就是一颗独立灯珠，不是文字、不会缩放，没有曲线、半像素或抗锯齿。'.' 是熄灭；A-F 对应 palette 的 #RRGGBB，最多 6 色。只回复一个紧凑、合法的 JSON 对象，不要 markdown。

先理解用户原话，再在心里制作分镜：谁是主体、哪些外形特征使它可辨认、起点与终点、运动方向、动作次序、是否往返及循环衔接。不得用通用爱心/星星替换用户指定主体。用户说“小兔子从左跳到右再从右跳到左”，作品必须让人看到两只分开的长耳、眼睛、身体、短尾和脚，实际完成左右往返，不能只是单张兔形图平移。别把主体缩成几个孤立灯点。

像素美术规则：先画清楚轮廓，再用 2-5 种有分工的颜色表现主体、眼睛/表情、阴影或动作尾迹；保留负空间，不把全部格子填满。主角在 8 行里占约 5-7 行，比例前后一致。同一作品的相邻画面需有有意的姿态变化，例如压低、起跳、腾空、伸腿、落地；次要细节可眨眼、摆尾、闪光。背景服从主体。循环最后一帧须能自然接回第一帧。参考手绘像素动画的关键姿态与停顿节奏，不要只复制静止图或仅改颜色。

三选一，按描述选最合适的格式：
1. 会移动的角色/物体用多姿态精灵：{"kind":"sprite","subject":"具体主体","fps":12,"motion":"hop_pingpong","palette":{"A":"#FFF0DC","B":"#FB8FA8","C":"#2C304C"},"poses":[["每行一个像素字符串",...],...],"background":["24字符",...]}
poses 必须绘制 6-8 张不同姿态，按 收身/起跳/上升/腾空/下落/落地 的连续顺序。每张恰好 6 或 7 行，所有行和所有姿态统一为 10-12 列；每张至少有 20 个亮点。姿态必须真的改变耳朵、四肢、表情或外轮廓，不能只更换一处颜色。程序会把姿态在实体 8×24 屏上展开为 64 帧，不需要你写 64 张整屏图。motion 取 hop_pingpong（弹跳往返）、pingpong（平移往返）、slide（单向经过）或 hop_in_place（原地弹跳）。用户说返回时必须选往返；用户说跳时必须选 hop_*。
background 可选：当场景适合时，另画恰好 8 行 × 24 列的稀疏静态背景，例如几颗草点、地面或远景星星，最多约 40 颗亮灯，不要挡住主角；主角会叠在背景上。未提及的复杂场景不要擅自添加。
2. 大场景、字样或条纹滚动用长画布：{"kind":"scroll","fps":10,"direction":"left","palette":{"A":"#55CCFF"},"canvas":[8 个等宽字符串]}。恰好 8 行，每行 48-96 列，逐列裁成 8×24 画面；画布中要有不同段落和细节，而不是重复同一个小图标。direction 为 left/right。
3. 原地变形、火焰、表情或多主体的小剧场用逐帧分镜：{"kind":"frames","fps":10,"palette":{"A":"#FF3366"},"frames":[[8 个长度恰好 24 的字符串],...],"holds":[2,2,...]}。绘制 8-16 张不同关键画面，按时间顺序画清动作起承转合；holds 与 frames 等长，每项 1-5，表示该画面保持几帧，合成 32-80 帧。该格式每行必须是 24 字符、每帧必须是 8 行。需要位移的主体若能用多姿态精灵表达，优先用格式 1，避免整屏逐帧消耗输出长度。

输出前自行核对：JSON 可解析；只出现 palette 中的字母；各行宽度正确；关键特征和运动方向与用户描述一致；画面之间有明显但连续的变化；循环可接回开头。"""


def load_settings(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return {"base_url": clean_base_url(str(data.get("base_url") or DEFAULT_BASE_URL)),
            "api_key": str(data.get("api_key") or ""),
            "model": str(data.get("model") or DEFAULT_MODEL)}


def save_settings(path: Path, settings: dict) -> None:
    settings = {**settings, "base_url": clean_base_url(settings["base_url"])}
    chat_url(settings["base_url"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, ensure_ascii=False, indent=1), encoding="utf-8")


def clean_base_url(value: str) -> str:
    """Tk Entry can retain invisible newlines from pasted endpoint addresses."""
    return re.sub(r"\s+", "", str(value or ""))


def chat_url(value: str) -> str:
    base = clean_base_url(value)
    parts = urlsplit(base)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("接口地址需要完整的 http(s):// 域名和路径")
    if parts.username or parts.password or parts.fragment:
        raise ValueError("接口地址不能包含账号、密码或片段")
    path = parts.path.rstrip("/")
    if not path.endswith("/chat/completions"):
        path += "/chat/completions"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))


def _stream_text(response, timeout: float) -> str:
    parts = []
    started = time.monotonic()
    for raw in response.iter_lines():
        if time.monotonic() - started > timeout:
            raise RuntimeError(f"生成超过 {timeout:g} 秒，请换用响应更快的模型")
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            delta = json.loads(data).get("choices", [{}])[0].get("delta", {})
        except (ValueError, IndexError, TypeError, AttributeError):
            continue
        content = delta.get("content") if isinstance(delta, dict) else None
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            parts.extend(str(item.get("text", "")) for item in content
                         if isinstance(item, dict) and item.get("type") == "text")
        if sum(map(len, parts)) > 200000:
            raise ValueError("AI 返回内容过长")
    if not parts:
        raise ValueError("接口没有返回可读取的动画内容")
    return "".join(parts)


def _chat(settings: dict, messages: list[dict], timeout: float, max_tokens: int,
          stream: bool = False) -> str:
    url = chat_url(settings["base_url"])
    headers = {"Content-Type": "application/json"}
    if settings.get("api_key"):
        headers["Authorization"] = f"Bearer {settings['api_key']}"
    body = {"model": settings["model"], "messages": messages, "max_tokens": max_tokens}
    if stream:
        body["stream"] = True
    for attempt in range(3):
        try:
            response = requests.post(url, headers=headers, json=body,
                                     timeout=(10, timeout), stream=stream,
                                     proxies={"http": "", "https": ""})
        except requests.exceptions.ConnectionError:
            # A direct connection may be unavailable on some networks. Only
            # retry connection failures through the OS proxy: a read timeout
            # may mean the model already received and charged the request.
            try:
                response = requests.post(url, headers=headers, json=body,
                                         timeout=(10, timeout), stream=stream)
            except requests.Timeout as exc:
                raise RuntimeError("直连失败，系统代理也连接或响应超时") from exc
            except requests.RequestException as exc:
                raise RuntimeError(f"直连与系统代理都连接失败：{exc}") from exc
        except requests.Timeout as exc:
            raise RuntimeError("接口连接或响应超时；请检查服务状态或换用响应更快的模型") from exc
        except requests.RequestException as exc:
            raise RuntimeError(f"连接失败：{exc}") from exc
        if response.status_code not in (400, 422):
            break
        try:
            error = response.json().get("error", {})
            detail = str(error.get("message", error) if isinstance(error, dict) else error)
        except (ValueError, TypeError, AttributeError):
            detail = ""
        if "max_tokens" not in detail and "max_completion_tokens" not in detail:
            break
        if attempt == 0:
            body["max_completion_tokens"] = body.pop("max_tokens")
        elif attempt == 1:
            body.pop("max_completion_tokens", None)
    if stream and response.ok and "text/event-stream" in response.headers.get("Content-Type", ""):
        try:
            return _stream_text(response, timeout)
        except requests.RequestException as exc:
            raise RuntimeError(f"流式生成中断：{exc}") from exc
        finally:
            response.close()
    if not response.ok:
        try:
            data = response.json()
            error = data.get("error", data) if isinstance(data, dict) else data
            detail = error.get("message", "") if isinstance(error, dict) else str(error)
        except (ValueError, TypeError):
            detail = ""
        raise RuntimeError(f"接口返回 HTTP {response.status_code}" +
                           (f"：{str(detail)[:180]}" if detail else ""))
    try:
        content = response.json()["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(str(item.get("text", "")) for item in content
                              if isinstance(item, dict) and item.get("type") == "text")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("模型没有返回文本内容")
        return content
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("接口响应不是兼容的 Chat Completions 格式") from exc


def test_connection(settings: dict) -> str:
    return _chat(settings, [{"role": "user", "content": "只回复：成功"}], 45, 64).strip()


def _extract_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError("AI 返回中没有 JSON")
    return json.loads(match.group())


def parse_design(text: str) -> tuple[int, list[bytes]]:
    """Validate an AI reply and convert it to (fps, HSV frames)."""
    data = _extract_json(text)
    palette = data.get("palette")
    if not isinstance(palette, dict) or not 1 <= len(palette) <= 6:
        raise ValueError("AI 返回的 palette 需要 1-6 种颜色")
    try:
        fps = int(data.get("fps", 6))
    except (TypeError, ValueError) as exc:
        raise ValueError("AI 返回的 fps 无效") from exc
    if not 1 <= fps <= 30:
        raise ValueError("AI 返回的 fps 需在 1-30 之间")
    colors = {}
    for key, value in palette.items():
        if str(key) not in "ABCDEF" or len(str(key)) != 1:
            raise ValueError(f"palette 键名无效：{key}")
        hex_value = str(value).lstrip("#")
        if len(hex_value) != 6:
            raise ValueError(f"palette 颜色无效：{value}")
        try:
            colors[str(key)] = tuple(int(hex_value[i:i+2], 16) for i in (0, 2, 4))
        except ValueError as exc:
            raise ValueError(f"palette 颜色无效：{value}") from exc
    kind = data.get("kind", "scroll" if "canvas" in data else "frames")
    if kind == "sprite":
        raw_poses = data.get("poses")
        if raw_poses is None:
            raw_poses = [data.get("sprite")]
        if not isinstance(raw_poses, list) or not raw_poses:
            raise ValueError("像素精灵缺少姿态画面")
        if len(raw_poses) != 1 and not 4 <= len(raw_poses) <= 8:
            raise ValueError("多姿态精灵需要 4-8 张姿态")
        poses = [_validate_sprite(pose, colors) for pose in raw_poses]
        if len({tuple(pose) for pose in poses}) < min(4, len(poses)):
            raise ValueError("精灵姿态重复太多，动作不明显")
        if any(len(pose) != len(poses[0]) or len(pose[0]) != len(poses[0][0])
               for pose in poses):
            raise ValueError("所有姿态必须使用同一尺寸")
        motion = data.get("motion", "pingpong")
        if motion not in ("hop_pingpong", "pingpong", "slide", "hop_in_place"):
            raise ValueError("像素精灵运动方式无效")
        background = data.get("background")
        if background is not None:
            background = _validate_grid_frames([background], colors)[0]
            if sum(char != "." for row in background for char in row) > 60:
                raise ValueError("背景灯点过密，会盖住主体")
        grid_frames = (_sprite_pose_frames(poses, motion, background) if len(poses) > 1
                       else _sprite_grid_frames(poses[0], motion, background))
    elif kind == "scroll":
        canvas = data.get("canvas")
        if not isinstance(canvas, list) or len(canvas) != ROWS or any(
                not isinstance(row, str) for row in canvas):
            raise ValueError("滚动画布需要 8 行字符串")
        width = len(canvas[0])
        if not COLS < width <= 96 or any(len(row) != width for row in canvas):
            raise ValueError("滚动画布每行需要相同的 25-96 列")
        direction = data.get("direction", "left")
        if direction not in ("left", "right"):
            raise ValueError("滚动方向只能是 left 或 right")
        if any(char != "." and char not in colors for row in canvas for char in row):
            raise ValueError("滚动画布使用了未定义颜色")
        grid_frames = [
            ["".join(row[(x + (phase if direction == "left" else -phase)) % width]
                     for x in range(COLS)) for row in canvas]
            for phase in range(width)
        ]
    elif kind == "frames":
        frames = data.get("frames")
        if not isinstance(frames, list) or not 2 <= len(frames) <= 32:
            raise ValueError("逐帧动画需要 2-32 帧，不能只返回一张图")
        grid_frames = _validate_grid_frames(frames, colors)
        holds = data.get("holds")
        if holds is not None:
            if (not isinstance(holds, list) or len(holds) != len(grid_frames)
                    or any(type(value) is not int or not 1 <= value <= 12 for value in holds)
                    or sum(holds) > MAX_FRAMES):
                raise ValueError("分镜保持帧数需与画面一一对应，总数不超过 200")
            grid_frames = [frame for frame, hold in zip(grid_frames, holds)
                           for _ in range(hold)]
    else:
        raise ValueError("动画类型只能是 sprite、frames 或 scroll")
    if all(char == "." for frame in grid_frames for row in frame for char in row):
        raise ValueError("AI 返回的画面是空白的")
    converted = [_grid_to_frame(frame, colors) for frame in grid_frames]
    if len(set(converted)) < 2:
        raise ValueError("AI 返回的各帧完全相同，无法形成动画")
    return fps, converted


def _validate_sprite(sprite, colors):
    if not isinstance(sprite, list) or len(sprite) not in (6, 7) or any(
            not isinstance(row, str) for row in sprite):
        raise ValueError("像素精灵需要 6-7 行")
    width = len(sprite[0])
    if not 10 <= width <= 12 or any(len(row) != width for row in sprite):
        raise ValueError("像素精灵每行需要相同的 10-12 列")
    if any(char != "." and char not in colors for row in sprite for char in row):
        raise ValueError("像素精灵使用了未定义颜色")
    if sum(any(char != "." for char in row) for row in sprite) < 5 or sum(
            char != "." for row in sprite for char in row) < 20:
        raise ValueError("像素精灵过小，主体轮廓无法辨认")
    return sprite


def _validate_grid_frames(frames, colors):
    grid_frames = []
    for frame in frames:
        if not isinstance(frame, list) or len(frame) != ROWS or any(
                not isinstance(row, str) for row in frame):
            raise ValueError("AI 返回的帧尺寸不是 8 行 × 24 列")
        if any(len(row) != COLS for row in frame):
            raise ValueError("AI 返回的帧尺寸不是 8 行 × 24 列")
        if any(char != "." and char not in colors for row in frame for char in row):
            raise ValueError("AI 返回的画面使用了未定义颜色")
        grid_frames.append(frame)
    return grid_frames


def _sprite_pose_frames(poses: list[list[str]], motion: str,
                        background: list[str] | None = None) -> list[list[str]]:
    """Play a drawn action cycle while moving one LED at a time across the screen."""
    height, width = len(poses[0]), len(poses[0][0])
    travel = COLS - width
    frames = []
    hop_steps = (0, 0, 1, 2, 2, 1, 0, 0)
    for frame_index in range(64):
        beat = frame_index % 8
        pose = poses[round(beat * (len(poses) - 1) / 7)]
        if motion == "slide":
            left = round((COLS + width) * frame_index / 64) - width
            reverse = False
        elif motion == "hop_in_place":
            left = travel // 2
            reverse = False
        else:
            progress = frame_index if frame_index <= 32 else 64 - frame_index
            left = round(travel * progress / 32)
            reverse = frame_index >= 32
        hop = min(ROWS - height, hop_steps[beat]) if motion.startswith("hop_") else 0
        top = ROWS - height - hop
        grid = [list(row) for row in background] if background else [["."] * COLS for _ in range(ROWS)]
        for y, row in enumerate(pose):
            for x, char in enumerate(row):
                dest_x = left + (width - 1 - x if reverse else x)
                if char != "." and 0 <= dest_x < COLS:
                    grid[top + y][dest_x] = char
        frames.append(["".join(row) for row in grid])
    return frames


def _sprite_grid_frames(sprite: list[str], motion: str,
                        background: list[str] | None = None) -> list[list[str]]:
    """Move one recognizable sprite across the physical 8×24 LED grid."""
    width = len(sprite[0])
    height = len(sprite)
    frames = []
    for phase in range(24):
        if motion == "slide":
            left = round((COLS + width) * phase / 23) - width
            reverse = False
        elif motion == "hop_in_place":
            left = (COLS - width) // 2
            reverse = False
        else:
            progress = phase if phase <= 12 else 24 - phase
            left = round((COLS - width) * progress / 12)
            reverse = phase > 12
        hop = min(ROWS - height, (0, 1, 2, 2, 1, 0)[phase % 6]) \
            if motion.startswith("hop_") else 0
        top = ROWS - height - hop
        grid = [list(row) for row in background] if background else [["."] * COLS for _ in range(ROWS)]
        for y, row in enumerate(sprite):
            for x, char in enumerate(row):
                dest_x = left + (width - 1 - x if reverse else x)
                if char != "." and 0 <= dest_x < COLS and 0 <= top + y < ROWS:
                    grid[top + y][dest_x] = char
        frames.append(["".join(row) for row in grid])
    return frames


def _grid_to_frame(grid: list[str], colors: dict[str, tuple[int, int, int]]) -> bytes:
    pixels = bytearray(ROWS * COLS * 3)
    for y, row in enumerate(grid):
        for x, char in enumerate(row):
            if char == ".":
                continue
            red, green, blue = colors[char]
            if red + green + blue == 0:
                continue
            hue, sat, val = colorsys.rgb_to_hsv(red / 255, green / 255, blue / 255)
            if sat < 0.16:  # near-grey renders as red on this firmware
                hue = 0.13 if val >= 0.5 else 0.60
                sat = 0.35
            value = max(0.10, val)  # near-black colour pixels render bugged
            pixels[((y * COLS) + x) * 3:((y * COLS) + x) * 3 + 3] = bytes((
                round(hue * 255) & 255, round(sat * 255), round(value * 255)))
    return bytes(pixels)


def generate(settings: dict, prompt: str, timeout: float = 180.0) -> tuple[int, list[bytes]]:
    """Request a genuine sequence, with one repair attempt for malformed output."""
    rabbit_hint = ""
    if re.search(r"兔|rabbit|bunny", prompt, re.I):
        rabbit_hint = ("\n兔子轮廓参考（仅作构图约束，不能用星形代替）："
                       "...AA..AA... / ...AB..BA... / ....AAAA.... / ...AAAACAA.. / "
                       "..AAAAAAAAA. / .AAAAAAAAAA. / AA.AA..AA...。"
                       "两只竖耳在上，眼睛在头部，宽身体和短尾在下。")
    messages = [{"role": "system", "content": SPEC},
                {"role": "user", "content":
                 f"用户要的动画：{prompt}\n请严格画出这个主体和动作。"
                 f"优先输出多姿态精灵的 64 帧动作，或 8-16 张分镜加 holds。{rabbit_hint}"}]
    for attempt in range(2):
        text = _chat(settings, messages, timeout, 6000)
        try:
            data = _extract_json(text)
            result = parse_design(text)
            _validate_subject(prompt, data)
            _validate_animation_quality(prompt, data, result[1])
            return result
        except ValueError as exc:
            if attempt:
                raise ValueError(f"模型两次未返回有效动画：{exc}") from exc
            messages.append({"role": "user", "content":
                             f"上一版未达到要求：{exc}。请重画且保留原始描述的主体和动作。"
                             "移动主体要 6-8 张不同 poses、展开后 64 帧；原地画面要 8-16 张不同分镜并用 holds 达到至少 32 帧。只输出合法 JSON。"})
    raise RuntimeError("动画生成未完成")


def _validate_animation_quality(prompt: str, data: dict, frames: list[bytes]) -> None:
    """Prevent a valid but nearly static reply from being presented as finished."""
    kind = data.get("kind", "scroll" if "canvas" in data else "frames")
    if kind == "sprite":
        poses = data.get("poses")
        if not isinstance(poses, list) or len(poses) < 6:
            raise ValueError("移动主体至少需要 6 张不同姿态，不能只平移一张图")
        changes = [sum(left != right for row_a, row_b in zip(first, second)
                       for left, right in zip(row_a, row_b))
                   for first, second in zip(poses, poses[1:])]
        if sum(change >= 4 for change in changes) < len(changes) // 2:
            raise ValueError("相邻姿态只有零星灯点变化，请重画明显的肢体或轮廓动作")
        motion = data.get("motion")
        if re.search(r"跳|跃|hop|jump", prompt, re.I) and motion not in (
                "hop_pingpong", "hop_in_place"):
            raise ValueError("描述要求跳跃，motion 需要使用跳跃动作")
        if re.search(r"往返|来回|返回|再从.{0,8}回|back and forth", prompt, re.I) and motion not in (
                "hop_pingpong", "pingpong"):
            raise ValueError("描述要求往返，motion 需要走到终点再回来")
    elif kind == "scroll":
        if len(data["canvas"][0]) < 48:
            raise ValueError("长画布至少绘制 48 列，给滚动留出丰富场景")
    else:
        keyframes = data.get("frames", [])
        if len(keyframes) < 8 or len({tuple(frame) for frame in keyframes}) < 6:
            raise ValueError("逐帧分镜至少需要 8 张，其中至少 6 张画面不同")
    if len(frames) < 32:
        raise ValueError("展开后至少需要 32 帧，才能形成完整的动作段落")


def _validate_subject(prompt: str, data: dict) -> None:
    """Reject a rabbit that has become an unrecognizable generic icon."""
    if not re.search(r"兔|rabbit|bunny", prompt, re.I):
        return
    if data.get("kind") != "sprite":
        raise ValueError("移动的兔子必须使用像素精灵，以保留兔耳和身体")
    sprite = data.get("sprite") or (data.get("poses") or [None])[0]
    if not isinstance(sprite, list) or not sprite:
        raise ValueError("兔子需要完整的像素姿态")
    ear_row = sprite[0]
    ear_groups = re.findall(r"[^.]+", ear_row)
    if len(ear_groups) < 2 or max(sum(char != "." for char in row) for row in sprite[-3:]) < 8:
        raise ValueError("兔子需要两只分开的竖耳和宽身体，目前轮廓不像兔子")
