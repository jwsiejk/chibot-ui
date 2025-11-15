// app/static/js/ws/frame_parser.js
// Decodes incoming WS messages (JSON/msgpack/control frames) and forwards
// normalized frames to a handler.

export function createFrameParser({
  hubLog,
  logStage,
  connection,         // createWsConnection(...), if needed
  handleMessageFrame, // function(frame) from ws_client.js
}) {
  function normalizeIncomingFrame(frame) {
    // real implementation will be moved from ws_client.js
    return frame;
  }

  function processControlFrameObject(frame) {
    // real implementation will be moved from ws_client.js
    return normalizeIncomingFrame(frame);
  }

  function parseMessageData(data) {
    // real implementation will be moved from ws_client.js / connection.js
    // Should return a normalized frame or null.
    return null;
  }

  function handleRawMessageData(data) {
    const frame = parseMessageData(data);
    if (!frame) return;
    handleMessageFrame(frame);
  }

  return {
    normalizeIncomingFrame,
    processControlFrameObject,
    parseMessageData,
    handleRawMessageData,
  };
}
