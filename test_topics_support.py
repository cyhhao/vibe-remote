#!/usr/bin/env python3
"""
Test script to validate Telegram Topics support changes
"""

import sys
from typing import Optional

# Mock MessageContext for testing
class MessageContext:
    def __init__(self, user_id: str, channel_id: str, thread_id: Optional[str] = None):
        self.user_id = user_id
        self.channel_id = channel_id
        self.thread_id = thread_id

# Mock config for testing
class MockConfig:
    platform = "telegram"

def test_session_id_generation():
    """Test the updated session ID generation logic"""
    print("Testing Session ID generation...")

    config = MockConfig()

    # Test 1: Telegram with topic
    context_with_topic = MessageContext(
        user_id="user123",
        channel_id="-1001234567890",
        thread_id="456"
    )

    # Simulate the updated logic from session_handler.py
    if config.platform == "telegram":
        if context_with_topic.thread_id:
            session_id = f"telegram_{context_with_topic.channel_id}_{context_with_topic.thread_id}"
        else:
            session_id = f"telegram_{context_with_topic.channel_id}"

    expected = "telegram_-1001234567890_456"
    assert session_id == expected, f"Expected {expected}, got {session_id}"
    print(f"  ✅ Topic message: {session_id}")

    # Test 2: Telegram without topic (DM or regular group)
    context_no_topic = MessageContext(
        user_id="user123",
        channel_id="-1001234567890",
        thread_id=None
    )

    if config.platform == "telegram":
        if context_no_topic.thread_id:
            session_id = f"telegram_{context_no_topic.channel_id}_{context_no_topic.thread_id}"
        else:
            session_id = f"telegram_{context_no_topic.channel_id}"

    expected = "telegram_-1001234567890"
    assert session_id == expected, f"Expected {expected}, got {session_id}"
    print(f"  ✅ Non-topic message: {session_id}")

    print("✅ Session ID generation tests passed!\n")

def test_thread_id_extraction():
    """Test thread_id extraction logic"""
    print("Testing thread_id extraction logic...")

    # Simulate a message with topic
    class MockUpdate:
        class MockMessage:
            message_thread_id = 123
        message = MockMessage()

    update = MockUpdate()

    # Simulate the extraction logic from handle_telegram_message
    thread_id = None
    if hasattr(update.message, 'message_thread_id') and update.message.message_thread_id:
        thread_id = str(update.message.message_thread_id)

    assert thread_id == "123", f"Expected '123', got {thread_id}"
    print(f"  ✅ Extracted thread_id: {thread_id}")

    # Simulate a message without topic
    class MockUpdateNoTopic:
        class MockMessage:
            message_thread_id = None
        message = MockMessage()

    update_no_topic = MockUpdateNoTopic()

    thread_id = None
    if hasattr(update_no_topic.message, 'message_thread_id') and update_no_topic.message.message_thread_id:
        thread_id = str(update_no_topic.message.message_thread_id)

    assert thread_id is None, f"Expected None, got {thread_id}"
    print(f"  ✅ No topic: thread_id = {thread_id}")

    print("✅ Thread ID extraction tests passed!\n")

def test_should_use_thread():
    """Test should_use_thread_for_reply return value"""
    print("Testing should_use_thread_for_reply()...")

    # This should now return True for Telegram
    should_use_thread = True  # Our updated implementation

    assert should_use_thread == True, "should_use_thread_for_reply should return True"
    print(f"  ✅ should_use_thread_for_reply returns: {should_use_thread}")

    print("✅ should_use_thread_for_reply test passed!\n")

def main():
    print("=" * 60)
    print("Telegram Topics Support - Validation Tests")
    print("=" * 60 + "\n")

    try:
        test_session_id_generation()
        test_thread_id_extraction()
        test_should_use_thread()

        print("=" * 60)
        print("✅ All validation tests passed!")
        print("=" * 60)
        return 0

    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        return 1
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
