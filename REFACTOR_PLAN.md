# Refactor Plan: Unify OpenCode Poll Loop

## Current Architecture (Problem)

Two separate polling loops with duplicated logic:

1. **Main Poll Loop** (`_process_message`, line 1671-2223)
   - Polls for new messages after initial prompt
   - Detects question → sends buttons → exits function
   - Lives in its own function call stack

2. **Post-Answer Poll Loop** (`_process_question_answer`, line 1323-1454)
   - User clicks button → new HTTP request → new function call
   - Submits answer → polls for completion
   - Duplicates almost all logic from main loop

## Issues

1. **Code Duplication**: 90% identical polling logic in two places
2. **Inconsistent Behavior**: Originally had timeout in post-answer but not main
3. **Maintenance Burden**: Bug fixes need to be applied twice
4. **Conceptual Complexity**: Why two loops for the same task?

## Target Architecture (Solution)

Single unified polling loop that handles questions internally:

```python
async def _process_message(request):
    await server.prompt_async(...)
    
    # Single unified loop
    while True:
        messages = await server.list_messages(...)
        
        for message in messages:
            # Process tool calls
            for part in message.parts:
                if part.tool == "question":
                    if part.state.status != "completed":
                        # New question detected
                        await self._send_question_to_user(...)
                        # Wait for answer (blocks until user responds)
                        await self._wait_for_answer_event(session_id)
                        # Answer submitted, continue loop
                    # If status == "completed", answer already submitted, continue
                
                else:
                    # Other tool calls
                    await send_tool_call(...)
            
            # Emit intermediate messages
            if message.finish == "tool-calls":
                await send_message(...)
        
        # Check completion
        if last_message.finish != "tool-calls":
            break
        
        await asyncio.sleep(2.0)
    
    # Send final result
    await send_final_result(...)

async def _process_question_answer(request, pending):
    # Simplified: only submits answer
    await server.reply_question(...)
    
    # Signal main loop to continue
    event = self._question_answer_events.get(session_id)
    if event:
        event.set()
```

## Implementation Steps

### Step 1: Add event coordination
- [x] Add `_question_answer_events: Dict[str, asyncio.Event]` to class
- [ ] Create event when question detected
- [ ] Set event when answer submitted
- [ ] Clear event after processing

### Step 2: Refactor main loop to not exit on question
- [ ] Remove `return` after sending question buttons
- [ ] Add `await event.wait()` to block until answer
- [ ] Continue processing after answer submitted

### Step 3: Simplify _process_question_answer
- [ ] Remove entire post-answer polling loop
- [ ] Keep only answer submission logic
- [ ] Set event to resume main loop

### Step 4: Handle edge cases
- [ ] Timeout for waiting on answer (user never responds)
- [ ] Nested questions (question after question)
- [ ] Concurrent requests to same session
- [ ] Poll restoration on restart

### Step 5: Testing
- [ ] Test normal question flow
- [ ] Test nested questions
- [ ] Test timeout scenarios
- [ ] Test concurrent operations
- [ ] Test poll restoration

## Benefits

1. **Single Source of Truth**: One polling loop, one place to fix bugs
2. **Consistent Behavior**: Same timeout/error handling everywhere
3. **Simpler Mental Model**: One continuous flow instead of disconnected loops
4. **Easier Debugging**: Single execution path through the code
5. **Better Maintainability**: Changes only need to be made once

## Risks

1. **Breaking Changes**: Need thorough testing
2. **Complexity**: Event-based coordination might be tricky
3. **Edge Cases**: Concurrent questions, restarts, etc.

## Migration Strategy

1. Implement refactor on feature branch
2. Run extensive manual testing
3. Deploy to staging environment
4. Monitor for issues
5. Roll out gradually if possible
