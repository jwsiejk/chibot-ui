export function waitForIceGatheringComplete(peer: RTCPeerConnection): Promise<void> {
  if (peer.iceGatheringState === 'complete') {
    return Promise.resolve();
  }

  return new Promise((resolve) => {
    const handleStateChange = () => {
      if (peer.iceGatheringState !== 'complete') {
        return;
      }
      peer.removeEventListener('icegatheringstatechange', handleStateChange);
      resolve();
    };

    peer.addEventListener('icegatheringstatechange', handleStateChange);
  });
}
