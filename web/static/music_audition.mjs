'use strict';

// music_audition.mjs — pure browser-side audition math for the music web app.
// No DOM, no AudioContext, no side effects. Importable by node:test.
// The /music/audition route sends interleaved float32 stereo PCM (little-endian,
// L,R,L,R,...), 4 bytes per sample. render_audition_pcm returns (n_frames, 2).

// Bytes per float32 sample.
const BYTES_PER_SAMPLE = 4;

// ── Frame / byte conversions ──────────────────────────────────────────────────

/** Convert a PCM byte length to a frame count. Throws if not a whole frame.
 * @param {number} byteLength  Total bytes of interleaved PCM data.
 * @param {number} [channels=2]  Number of audio channels.
 * @returns {number} Frame count.
 */
export function frameCountFromByteLength(byteLength, channels = 2) {
  const bytesPerFrame = channels * BYTES_PER_SAMPLE;
  if (byteLength % bytesPerFrame !== 0) {
    throw new RangeError(
      `byteLength ${byteLength} is not a whole number of frames ` +
      `(bytesPerFrame=${bytesPerFrame})`,
    );
  }
  return byteLength / bytesPerFrame;
}

/** Convert a frame count to the PCM byte length for interleaved data.
 * @param {number} frameCount  Number of audio frames.
 * @param {number} [channels=2]  Number of audio channels.
 * @returns {number} Byte length.
 */
export function byteLengthFromFrameCount(frameCount, channels = 2) {
  return frameCount * channels * BYTES_PER_SAMPLE;
}

// ── Deinterleave ──────────────────────────────────────────────────────────────

/** Split an interleaved Float32Array into per-channel Float32Arrays.
 * @param {Float32Array} interleavedFloat32  L,R,L,R,... samples.
 * @param {number} [channels=2]  Number of interleaved channels.
 * @returns {Float32Array[]}  Array of length `channels`, each of length frameCount.
 */
export function deinterleave(interleavedFloat32, channels = 2) {
  const frameCount = interleavedFloat32.length / channels;
  const result = Array.from({ length: channels }, () => new Float32Array(frameCount));
  for (let frame = 0; frame < frameCount; frame++) {
    for (let ch = 0; ch < channels; ch++) {
      result[ch][frame] = interleavedFloat32[frame * channels + ch];
    }
  }
  return result;
}

// ── Fade math ─────────────────────────────────────────────────────────────────

/** Compute the linear fade gain for a single frame position.
 * Gain ramps from 0→1 over [0, fadeInFrames) and from 1→0 over
 * [totalFrames-fadeOutFrames, totalFrames). The steady middle returns 1.
 * Gain is clamped to [0, 1].
 * @param {number} frameIndex     Zero-based position in the clip.
 * @param {number} totalFrames    Total clip length in frames.
 * @param {number} fadeInFrames   Length of the linear fade-in ramp (frames).
 * @param {number} fadeOutFrames  Length of the linear fade-out ramp (frames).
 * @returns {number}  Gain in [0, 1].
 */
export function fadeGainAtFrame(frameIndex, totalFrames, fadeInFrames, fadeOutFrames) {
  const fadeOutStart = totalFrames - fadeOutFrames;
  let gain = 1;
  if (fadeInFrames > 0 && frameIndex < fadeInFrames) {
    gain = Math.min(gain, frameIndex / fadeInFrames);
  }
  if (fadeOutFrames > 0 && frameIndex >= fadeOutStart) {
    const framesIntoFadeOut = frameIndex - fadeOutStart;
    gain = Math.min(gain, 1 - framesIntoFadeOut / fadeOutFrames);
  }
  return Math.max(0, Math.min(1, gain));
}

/** Apply a linear fade-in / fade-out envelope to an array of per-channel Float32Arrays.
 * Returns a new array of new Float32Arrays — does not mutate the inputs.
 * @param {Float32Array[]} channelData    One Float32Array per channel.
 * @param {number} fadeInFrames   Length of the linear fade-in ramp.
 * @param {number} fadeOutFrames  Length of the linear fade-out ramp.
 * @returns {Float32Array[]}  New per-channel arrays with the envelope applied.
 */
export function applyFadeInOut(channelData, fadeInFrames, fadeOutFrames) {
  const totalFrames = channelData[0].length;
  return channelData.map(ch => {
    const out = new Float32Array(totalFrames);
    for (let i = 0; i < totalFrames; i++) {
      out[i] = ch[i] * fadeGainAtFrame(i, totalFrames, fadeInFrames, fadeOutFrames);
    }
    return out;
  });
}

// ── Cancel/replace audition bookkeeping ──────────────────────────────────────

/** Return the next monotonically increasing audition token.
 * A control change calls this; any in-flight result bearing an older token
 * should be discarded via isCurrent().
 * @param {number} prevToken  The previous token (or 0 on first use).
 * @returns {number}  prevToken + 1.
 */
export function nextAuditionToken(prevToken) {
  return prevToken + 1;
}

/** Return true only if `token` matches `latestToken` (i.e. the result is still current).
 * @param {number} token        Token carried by the in-flight result.
 * @param {number} latestToken  The most recently issued token.
 * @returns {boolean}
 */
export function isCurrent(token, latestToken) {
  return token === latestToken;
}
