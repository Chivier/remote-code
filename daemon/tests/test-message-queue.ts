import { describe, it, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { MessageQueue } from "../src/message-queue";
import { StreamEvent } from "../src/types";

describe("MessageQueue", () => {
  let queue: MessageQueue;

  beforeEach(() => {
    queue = new MessageQueue();
  });

  // ─── enqueueUser ───

  describe("enqueueUser()", () => {
    it("returns 1 for the first enqueued message", () => {
      const pos = queue.enqueueUser("hello");
      assert.equal(pos, 1);
    });

    it("returns incrementing positions for subsequent messages", () => {
      assert.equal(queue.enqueueUser("first"), 1);
      assert.equal(queue.enqueueUser("second"), 2);
      assert.equal(queue.enqueueUser("third"), 3);
    });

    it("positions reset after dequeue and re-enqueue", () => {
      queue.enqueueUser("first");
      queue.enqueueUser("second");
      queue.dequeueUser(); // removes first, length becomes 1
      // After dequeuing one, length is 1, so next enqueue makes it 2
      assert.equal(queue.enqueueUser("third"), 2);
    });
  });

  // ─── dequeueUser ───

  describe("dequeueUser()", () => {
    it("returns null when queue is empty", () => {
      assert.equal(queue.dequeueUser(), null);
    });

    it("returns messages in FIFO order", () => {
      queue.enqueueUser("first");
      queue.enqueueUser("second");
      queue.enqueueUser("third");

      const msg1 = queue.dequeueUser();
      assert.notEqual(msg1, null);
      assert.equal(msg1!.message, "first");

      const msg2 = queue.dequeueUser();
      assert.notEqual(msg2, null);
      assert.equal(msg2!.message, "second");

      const msg3 = queue.dequeueUser();
      assert.notEqual(msg3, null);
      assert.equal(msg3!.message, "third");

      assert.equal(queue.dequeueUser(), null);
    });

    it("returned messages have timestamp set", () => {
      const before = Date.now();
      queue.enqueueUser("hello");
      const after = Date.now();

      const msg = queue.dequeueUser();
      assert.notEqual(msg, null);
      assert.ok(msg!.timestamp >= before);
      assert.ok(msg!.timestamp <= after);
    });
  });

  // ─── hasUserPending ───

  describe("hasUserPending()", () => {
    it("returns false when queue is empty", () => {
      assert.equal(queue.hasUserPending(), false);
    });

    it("returns true when messages are queued", () => {
      queue.enqueueUser("hello");
      assert.equal(queue.hasUserPending(), true);
    });

    it("returns false after all messages are dequeued", () => {
      queue.enqueueUser("hello");
      queue.dequeueUser();
      assert.equal(queue.hasUserPending(), false);
    });
  });

  // ─── userQueueLength ───

  describe("userQueueLength", () => {
    it("is 0 initially", () => {
      assert.equal(queue.userQueueLength, 0);
    });

    it("increments with enqueue", () => {
      queue.enqueueUser("a");
      assert.equal(queue.userQueueLength, 1);
      queue.enqueueUser("b");
      assert.equal(queue.userQueueLength, 2);
    });

    it("decrements with dequeue", () => {
      queue.enqueueUser("a");
      queue.enqueueUser("b");
      queue.dequeueUser();
      assert.equal(queue.userQueueLength, 1);
    });

    it("does not go below 0", () => {
      queue.dequeueUser();
      assert.equal(queue.userQueueLength, 0);
    });
  });

  // ─── bufferResponse ───

  describe("bufferResponse()", () => {
    const textEvent: StreamEvent = { type: "text", content: "hello" };
    const toolEvent: StreamEvent = { type: "tool_use", tool: "bash" };

    it("does NOT buffer when client is connected (default state)", () => {
      queue.bufferResponse(textEvent);
      const events = queue.replayResponses();
      assert.equal(events.length, 0);
    });

    it("buffers when client is disconnected", () => {
      queue.onClientDisconnect();
      queue.bufferResponse(textEvent);
      queue.bufferResponse(toolEvent);
      const events = queue.replayResponses();
      assert.equal(events.length, 2);
      assert.deepEqual(events[0], textEvent);
      assert.deepEqual(events[1], toolEvent);
    });

    it("buffers when force=true even if client is connected", () => {
      // Client is connected by default
      assert.equal(queue.clientConnected, true);
      queue.bufferResponse(textEvent, true);
      queue.bufferResponse(toolEvent, true);
      const events = queue.replayResponses();
      assert.equal(events.length, 2);
      assert.deepEqual(events[0], textEvent);
      assert.deepEqual(events[1], toolEvent);
    });

    it("does NOT buffer when force=false and client is connected", () => {
      queue.bufferResponse(textEvent, false);
      const events = queue.replayResponses();
      assert.equal(events.length, 0);
    });
  });

  // ─── replayResponses ───

  describe("replayResponses()", () => {
    it("returns empty array when nothing is buffered", () => {
      const events = queue.replayResponses();
      assert.deepEqual(events, []);
    });

    it("returns all buffered events in order", () => {
      queue.onClientDisconnect();
      const e1: StreamEvent = { type: "text", content: "one" };
      const e2: StreamEvent = { type: "partial", content: "two" };
      const e3: StreamEvent = { type: "result", session_id: "abc" };
      queue.bufferResponse(e1);
      queue.bufferResponse(e2);
      queue.bufferResponse(e3);

      const events = queue.replayResponses();
      assert.equal(events.length, 3);
      assert.deepEqual(events[0], e1);
      assert.deepEqual(events[1], e2);
      assert.deepEqual(events[2], e3);
    });

    it("clears the buffer after replay", () => {
      queue.onClientDisconnect();
      queue.bufferResponse({ type: "text", content: "hello" });
      queue.replayResponses();

      const secondReplay = queue.replayResponses();
      assert.deepEqual(secondReplay, []);
    });
  });

  // ─── hasResponsesPending ───

  describe("hasResponsesPending()", () => {
    it("returns false when no responses are buffered", () => {
      assert.equal(queue.hasResponsesPending(), false);
    });

    it("returns true when responses are buffered", () => {
      queue.onClientDisconnect();
      queue.bufferResponse({ type: "text", content: "data" });
      assert.equal(queue.hasResponsesPending(), true);
    });

    it("returns false after replayResponses clears the buffer", () => {
      queue.onClientDisconnect();
      queue.bufferResponse({ type: "text", content: "data" });
      queue.replayResponses();
      assert.equal(queue.hasResponsesPending(), false);
    });
  });

  // ─── clientConnected ───

  describe("clientConnected", () => {
    it("is true by default", () => {
      assert.equal(queue.clientConnected, true);
    });

    it("becomes false after onClientDisconnect()", () => {
      queue.onClientDisconnect();
      assert.equal(queue.clientConnected, false);
    });

    it("becomes true again after onClientReconnect()", () => {
      queue.onClientDisconnect();
      queue.onClientReconnect();
      assert.equal(queue.clientConnected, true);
    });
  });

  // ─── onClientDisconnect / onClientReconnect ───

  describe("onClientDisconnect()", () => {
    it("sets clientConnected to false", () => {
      queue.onClientDisconnect();
      assert.equal(queue.clientConnected, false);
    });

    it("causes subsequent bufferResponse calls to buffer", () => {
      queue.onClientDisconnect();
      queue.bufferResponse({ type: "text", content: "buffered" });
      assert.equal(queue.hasResponsesPending(), true);
    });

    it("can be called multiple times without error", () => {
      queue.onClientDisconnect();
      queue.onClientDisconnect();
      assert.equal(queue.clientConnected, false);
    });
  });

  describe("onClientReconnect()", () => {
    it("sets clientConnected to true", () => {
      queue.onClientDisconnect();
      queue.onClientReconnect();
      assert.equal(queue.clientConnected, true);
    });

    it("returns buffered events", () => {
      queue.onClientDisconnect();
      const event: StreamEvent = { type: "text", content: "reconnect data" };
      queue.bufferResponse(event);

      const replayed = queue.onClientReconnect();
      assert.equal(replayed.length, 1);
      assert.deepEqual(replayed[0], event);
    });

    it("clears the buffer after returning events", () => {
      queue.onClientDisconnect();
      queue.bufferResponse({ type: "text", content: "data" });
      queue.onClientReconnect();

      // Buffer should be empty now
      assert.equal(queue.hasResponsesPending(), false);
    });

    it("returns empty array when no events were buffered", () => {
      queue.onClientDisconnect();
      const replayed = queue.onClientReconnect();
      assert.deepEqual(replayed, []);
    });

    it("after reconnect, bufferResponse no longer buffers (without force)", () => {
      queue.onClientDisconnect();
      queue.onClientReconnect();
      queue.bufferResponse({ type: "text", content: "not buffered" });
      assert.equal(queue.hasResponsesPending(), false);
    });
  });

  // ─── clear ───

  describe("clear()", () => {
    it("empties the user queue", () => {
      queue.enqueueUser("a");
      queue.enqueueUser("b");
      queue.clear();
      assert.equal(queue.userQueueLength, 0);
      assert.equal(queue.hasUserPending(), false);
      assert.equal(queue.dequeueUser(), null);
    });

    it("empties the response buffer", () => {
      queue.onClientDisconnect();
      queue.bufferResponse({ type: "text", content: "data" });
      queue.clear();
      assert.equal(queue.hasResponsesPending(), false);
      assert.deepEqual(queue.replayResponses(), []);
    });

    it("empties both queues simultaneously", () => {
      queue.enqueueUser("msg");
      queue.onClientDisconnect();
      queue.bufferResponse({ type: "text", content: "resp" });

      queue.clear();

      assert.equal(queue.userQueueLength, 0);
      assert.equal(queue.hasResponsesPending(), false);
    });

    it("does not change clientConnected state", () => {
      queue.onClientDisconnect();
      queue.clear();
      assert.equal(queue.clientConnected, false);
    });
  });

  // ─── stats ───

  describe("stats()", () => {
    it("returns zeros for empty queue", () => {
      const s = queue.stats();
      assert.deepEqual(s, {
        userPending: 0,
        responsePending: 0,
        clientConnected: true,
      });
    });

    it("reflects user queue count", () => {
      queue.enqueueUser("a");
      queue.enqueueUser("b");
      const s = queue.stats();
      assert.equal(s.userPending, 2);
    });

    it("reflects response buffer count", () => {
      queue.onClientDisconnect();
      queue.bufferResponse({ type: "text", content: "x" });
      queue.bufferResponse({ type: "text", content: "y" });
      queue.bufferResponse({ type: "text", content: "z" });
      const s = queue.stats();
      assert.equal(s.responsePending, 3);
    });

    it("reflects clientConnected state", () => {
      assert.equal(queue.stats().clientConnected, true);
      queue.onClientDisconnect();
      assert.equal(queue.stats().clientConnected, false);
      queue.onClientReconnect();
      assert.equal(queue.stats().clientConnected, true);
    });

    it("accurately reflects state after mixed operations", () => {
      queue.enqueueUser("m1");
      queue.enqueueUser("m2");
      queue.enqueueUser("m3");
      queue.dequeueUser(); // removes m1

      queue.onClientDisconnect();
      queue.bufferResponse({ type: "text", content: "r1" });

      const s = queue.stats();
      assert.equal(s.userPending, 2);
      assert.equal(s.responsePending, 1);
      assert.equal(s.clientConnected, false);
    });
  });
});
