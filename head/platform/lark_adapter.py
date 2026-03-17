"""
Lark (Feishu) adapter implementing PlatformAdapter protocol.

Uses lark-oapi SDK with WebSocket long-connection mode:
- Receives events via WebSocket (no public IP needed)
- Sends messages via OpenAPI REST
- Supports rich text (post) messages and file uploads

Typing indicator is a no-op — Feishu has no such API.
"""

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

from head.config import LarkConfig
from head.message_formatter import split_message
from head.platform.protocol import (
    PlatformAdapter,
    MessageHandle,
    FileAttachment,
    InputHandler,
)

logger = logging.getLogger(__name__)


def markdown_to_lark_post(text: str) -> dict:
    """Convert markdown text to Feishu post message structure.

    Supports:
    - **bold** -> bold tag
    - `code` -> code inline (text tag with style)
    - ```code blocks``` -> code block lines
    - [link](url) -> link tag (a tag)
    - Plain text -> text tag

    Returns: {"zh_cn": {"title": "", "content": [[...tags...]]}}
    """
    content: list[list[dict]] = []

    # Split by code blocks first
    parts = re.split(r"(```\w*\n.*?```)", text, flags=re.DOTALL)

    for part in parts:
        # Code block
        code_match = re.match(r"```\w*\n?(.*?)```", part, re.DOTALL)
        if code_match:
            code_text = code_match.group(1).rstrip("\n")
            for line in code_text.split("\n"):
                content.append([{"tag": "text", "text": line, "style": ["code_block"]}])
            continue

        # Process regular text line by line
        lines = part.split("\n")
        for line in lines:
            if not line:
                content.append([{"tag": "text", "text": ""}])
                continue

            tags = _parse_inline_markdown(line)
            if tags:
                content.append(tags)

    return {"zh_cn": {"title": "", "content": content}}


def _parse_inline_markdown(text: str) -> list[dict]:
    """Parse inline markdown (bold, code, links) into Feishu post tags."""
    tags: list[dict] = []

    # Pattern to match: **bold**, `code`, [text](url)
    pattern = re.compile(
        r"(\*\*(.+?)\*\*)"  # bold
        r"|(`([^`]+)`)"  # inline code
        r"|(\[([^\]]+)\]\(([^)]+)\))"  # link
    )

    last_end = 0
    for match in pattern.finditer(text):
        # Add plain text before this match
        if match.start() > last_end:
            plain = text[last_end : match.start()]
            if plain:
                tags.append({"tag": "text", "text": plain})

        if match.group(2):  # bold
            tags.append({"tag": "text", "text": match.group(2), "style": ["bold"]})
        elif match.group(4):  # inline code
            tags.append({"tag": "text", "text": match.group(4), "style": ["code_block"]})
        elif match.group(6):  # link
            tags.append({"tag": "a", "text": match.group(6), "href": match.group(7)})

        last_end = match.end()

    # Add remaining plain text
    if last_end < len(text):
        remaining = text[last_end:]
        if remaining:
            tags.append({"tag": "text", "text": remaining})

    return tags


class LarkAdapter:
    """Feishu/Lark platform adapter using WebSocket long-connection."""

    def __init__(self, config: LarkConfig):
        self._config = config
        self._client: Any = None  # lark.Client (REST)
        self._ws_client: Any = None  # lark.ws.Client (WebSocket)
        self._on_input: Optional[InputHandler] = None
        self._stop_event: Optional[asyncio.Event] = None

    @property
    def platform_name(self) -> str:
        return "lark"

    @property
    def max_message_length(self) -> int:
        return 30000

    # --- Message Operations ---

    async def send_message(self, channel_id: str, text: str) -> MessageHandle:
        """Send a rich text (post) message to a Feishu chat."""
        if not self._client:
            logger.warning("Lark client not initialized")
            return MessageHandle(platform="lark", channel_id=channel_id, message_id="0")

        import lark_oapi as lark
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

        chat_id = self._chat_id_from_channel(channel_id)
        chunks = split_message(text, max_len=30000)
        last_handle = MessageHandle(platform="lark", channel_id=channel_id, message_id="0")

        for chunk in chunks:
            try:
                post_content = markdown_to_lark_post(chunk)
                body = CreateMessageRequestBody.builder() \
                    .receive_id(chat_id) \
                    .msg_type("post") \
                    .content(json.dumps(post_content)) \
                    .build()
                request = CreateMessageRequest.builder() \
                    .receive_id_type("chat_id") \
                    .request_body(body) \
                    .build()

                response = self._client.im.v1.message.create(request)
                if response.success():
                    msg_id = response.data.message_id
                    last_handle = MessageHandle(
                        platform="lark",
                        channel_id=channel_id,
                        message_id=msg_id,
                        raw=response.data,
                    )
                else:
                    logger.error(f"Failed to send Lark message: {response.msg}")
            except Exception as e:
                logger.error(f"Error sending Lark message: {e}")

        return last_handle

    async def edit_message(self, handle: MessageHandle, text: str) -> None:
        """Edit an existing Feishu message via PATCH API."""
        if not self._client:
            return

        import lark_oapi as lark
        from lark_oapi.api.im.v1 import PatchMessageRequest, PatchMessageRequestBody

        try:
            post_content = markdown_to_lark_post(text)
            body = PatchMessageRequestBody.builder() \
                .msg_type("post") \
                .content(json.dumps(post_content)) \
                .build()
            request = PatchMessageRequest.builder() \
                .message_id(handle.message_id) \
                .request_body(body) \
                .build()

            response = self._client.im.v1.message.patch(request)
            if not response.success():
                logger.warning(f"Failed to edit Lark message: {response.msg}")
        except Exception as e:
            logger.warning(f"Error editing Lark message: {e}")

    async def delete_message(self, handle: MessageHandle) -> None:
        """Delete a Feishu message via DELETE API."""
        if not self._client:
            return

        from lark_oapi.api.im.v1 import DeleteMessageRequest

        try:
            request = DeleteMessageRequest.builder() \
                .message_id(handle.message_id) \
                .build()

            response = self._client.im.v1.message.delete(request)
            if not response.success():
                logger.warning(f"Failed to delete Lark message: {response.msg}")
        except Exception as e:
            logger.warning(f"Error deleting Lark message: {e}")

    # --- File Operations ---

    async def download_file(self, attachment: FileAttachment, dest: Path) -> Path:
        """Download a file attachment from a Feishu message."""
        if not self._client:
            raise RuntimeError("Lark client not initialized")

        from lark_oapi.api.im.v1 import GetMessageResourceRequest

        try:
            request = GetMessageResourceRequest.builder() \
                .message_id(attachment.platform_ref.get("message_id", "")) \
                .file_key(attachment.platform_ref.get("file_key", "")) \
                .type(attachment.platform_ref.get("type", "file")) \
                .build()

            response = self._client.im.v1.message_resource.get(request)
            if response.success():
                dest.write_bytes(response.file.read())
                return dest
            else:
                raise RuntimeError(f"Failed to download: {response.msg}")
        except Exception as e:
            raise RuntimeError(f"Failed to download Lark file: {e}")

    async def send_file(self, channel_id: str, path: Path, caption: str = "") -> MessageHandle:
        """Upload and send a file to a Feishu chat.

        Auto-detects image types and uses image API; others use file API.
        """
        if not self._client:
            raise RuntimeError("Lark client not initialized")

        from lark_oapi.api.im.v1 import (
            CreateMessageRequest,
            CreateMessageRequestBody,
            CreateFileRequest,
            CreateFileRequestBody,
            CreateImageRequest,
            CreateImageRequestBody,
        )

        chat_id = self._chat_id_from_channel(channel_id)

        # Detect if image
        suffix = path.suffix.lower()
        image_types = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
        is_image = suffix in image_types

        try:
            if is_image:
                # Upload as image
                with open(path, "rb") as f:
                    body = CreateImageRequestBody.builder() \
                        .image_type("message") \
                        .image(f) \
                        .build()
                    request = CreateImageRequest.builder() \
                        .request_body(body) \
                        .build()
                    response = self._client.im.v1.image.create(request)

                if not response.success():
                    raise RuntimeError(f"Image upload failed: {response.msg}")

                image_key = response.data.image_key
                content = json.dumps({"image_key": image_key})
                msg_type = "image"
            else:
                # Upload as file
                with open(path, "rb") as f:
                    body = CreateFileRequestBody.builder() \
                        .file_type("stream") \
                        .file_name(path.name) \
                        .file(f) \
                        .build()
                    request = CreateFileRequest.builder() \
                        .request_body(body) \
                        .build()
                    response = self._client.im.v1.file.create(request)

                if not response.success():
                    raise RuntimeError(f"File upload failed: {response.msg}")

                file_key = response.data.file_key
                content = json.dumps({"file_key": file_key})
                msg_type = "file"

            # Send the file/image message
            msg_body = CreateMessageRequestBody.builder() \
                .receive_id(chat_id) \
                .msg_type(msg_type) \
                .content(content) \
                .build()
            msg_request = CreateMessageRequest.builder() \
                .receive_id_type("chat_id") \
                .request_body(msg_body) \
                .build()

            msg_response = self._client.im.v1.message.create(msg_request)
            if msg_response.success():
                # Send caption as a separate text message if provided
                if caption:
                    await self.send_message(channel_id, caption)
                return MessageHandle(
                    platform="lark",
                    channel_id=channel_id,
                    message_id=msg_response.data.message_id,
                    raw=msg_response.data,
                )
            else:
                raise RuntimeError(f"Failed to send file message: {msg_response.msg}")

        except Exception as e:
            logger.error(f"Error sending Lark file: {e}")
            raise

    # --- Interaction State ---

    async def start_typing(self, channel_id: str) -> None:
        """No-op. Feishu has no typing indicator API."""
        pass

    async def stop_typing(self, channel_id: str) -> None:
        """No-op."""
        pass

    # --- Capability Queries ---

    def supports_message_edit(self) -> bool:
        return True

    def supports_inline_buttons(self) -> bool:
        return True

    def supports_file_upload(self) -> bool:
        return True

    # --- Input Callback ---

    def set_input_handler(self, handler: InputHandler) -> None:
        """Set the callback invoked when a user message arrives."""
        self._on_input = handler

    # --- Lifecycle ---

    async def start(self) -> None:
        """Initialize Lark clients, register event handlers, start WS."""
        import lark_oapi as lark
        from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

        logger.info("Starting Lark bot...")

        self._client = lark.Client.builder() \
            .app_id(self._config.app_id) \
            .app_secret(self._config.app_secret) \
            .log_level(lark.LogLevel.WARNING) \
            .build()

        # Set up event handler for incoming messages
        event_handler = lark.EventDispatcherHandler.builder("", "") \
            .register_p2_im_message_receive_v1(self._handle_message_event) \
            .build()

        self._ws_client = lark.ws.Client(
            self._config.app_id,
            self._config.app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.WARNING,
        )

        self._stop_event = asyncio.Event()

        # Start WebSocket in a thread (lark-oapi WS client is synchronous)
        loop = asyncio.get_running_loop()
        self._ws_task = loop.run_in_executor(None, self._ws_client.start)

        logger.info("Lark bot started")
        await self._stop_event.wait()

    async def stop(self) -> None:
        """Stop the Lark bot."""
        logger.info("Stopping Lark bot...")
        if self._stop_event:
            self._stop_event.set()

    # --- Internal Helpers ---

    def _chat_id_from_channel(self, channel_id: str) -> str:
        """Extract Feishu chat ID from internal channel ID."""
        return channel_id.split(":", 1)[1]

    def _channel_id(self, chat_id: str) -> str:
        """Build internal channel ID from Feishu chat ID."""
        return f"lark:{chat_id}"

    def _is_allowed_chat(self, chat_id: str) -> bool:
        """Check if a Feishu chat is allowed."""
        if not self._config.allowed_chats:
            return True
        return chat_id in self._config.allowed_chats

    def _handle_message_event(self, data: Any) -> None:
        """Handle incoming Feishu message event (called from WS thread)."""
        try:
            event = data.event
            msg = event.message
            sender = event.sender

            # Ignore bot's own messages
            if sender.sender_type == "app":
                return

            chat_id = msg.chat_id
            sender_id = sender.sender_id.open_id if sender.sender_id else None

            # Filter by allowed chats
            if not self._is_allowed_chat(chat_id):
                return

            # Parse message content
            try:
                content = json.loads(msg.content)
                text = content.get("text", "")
            except (json.JSONDecodeError, AttributeError):
                return

            if not text:
                return

            channel_id = self._channel_id(chat_id)

            # Extract file attachments if any (image/file messages)
            attachments = self._extract_attachments(msg)

            # Dispatch to engine via the input handler
            if self._on_input:
                loop = asyncio.get_event_loop()
                asyncio.run_coroutine_threadsafe(
                    self._on_input(channel_id, text, sender_id, attachments or None),
                    loop,
                )
        except Exception as e:
            logger.error(f"Error handling Lark message event: {e}")

    def _extract_attachments(self, msg: Any) -> list[FileAttachment]:
        """Extract file attachments from a Feishu message."""
        attachments: list[FileAttachment] = []

        msg_type = getattr(msg, "message_type", "text")
        if msg_type not in ("image", "file"):
            return attachments

        try:
            content = json.loads(msg.content)
            message_id = msg.message_id

            if msg_type == "image":
                image_key = content.get("image_key", "")
                if image_key:
                    attachments.append(
                        FileAttachment(
                            filename=f"{image_key}.png",
                            size=0,
                            mime_type="image/png",
                            url=None,
                            platform_ref={
                                "message_id": message_id,
                                "file_key": image_key,
                                "type": "image",
                            },
                        )
                    )
            elif msg_type == "file":
                file_key = content.get("file_key", "")
                file_name = content.get("file_name", "file")
                if file_key:
                    attachments.append(
                        FileAttachment(
                            filename=file_name,
                            size=0,
                            mime_type=None,
                            url=None,
                            platform_ref={
                                "message_id": message_id,
                                "file_key": file_key,
                                "type": "file",
                            },
                        )
                    )
        except Exception as e:
            logger.warning(f"Failed to extract Lark attachments: {e}")

        return attachments
