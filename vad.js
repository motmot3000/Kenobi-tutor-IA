"use strict";
// Détection de la parole sur le niveau du micro (RMS), adaptative au bruit ambiant.
// Voice activity on the microphone RMS, fed by the page every ~40 ms.
// The gate follows the room: the noise floor is the lowest smoothed level of
// the last seconds (speech always has short dips, steady noise does not), so
// a fan or a street raises the threshold instead of holding the turn open.
// While the agent speaks, a higher gate and a longer onset are required, so its
// own voice left after echo cancellation does not interrupt it.
// RMS is NOT speaker recognition.
class VoiceActivity {
  constructor({threshold = 0.01, ratio = 3, startMs = 160, bargeMs = 280, endMs = 700, maxMs = 43000,
    bargeGate = 0.05, floorWindowMs = 4000, floorMax = 0.03} = {}) {
    Object.assign(this, {threshold, ratio, startMs, bargeMs, endMs, maxMs, bargeGate, floorWindowMs, floorMax});
    this.floor = 0; this.lastVoicedMs = 0;
    this.smooth = null; this.mins = []; this.block = null;
    this.reset();
  }
  reset() {
    this.speaking = false; this.above = null; this.below = null; this.started = null;
    this.voicedMs = 0; this.last = null;
  }
  gate(playback = false) {
    const base = Math.max(this.threshold, this.floor * this.ratio);
    return playback ? Math.max(this.bargeGate, base * 2.5) : base;
  }
  learn(rms, now) {
    this.smooth = this.smooth === null ? rms : this.smooth + (rms - this.smooth) * 0.35;
    if (!this.block || now - this.block.at >= 250) {
      if (this.block) this.mins.push(this.block.min);
      const keep = Math.ceil(this.floorWindowMs / 250);
      if (this.mins.length > keep) this.mins.splice(0, this.mins.length - keep);
      this.block = {at:now, min:Infinity};
    }
    this.block.min = Math.min(this.block.min, this.smooth);
    this.floor = Math.min(this.floorMax, this.block.min, ...this.mins);
  }
  tick(rms, now, playback = false) {
    if (!Number.isFinite(rms)) return null;
    const dt = this.last === null ? 0 : Math.max(0, Math.min(120, now - this.last));
    this.last = now;
    if (!playback) this.learn(rms, now); // the agent's echo is not the room
    const gate = this.gate(playback);
    const loud = rms >= gate * (this.speaking ? 0.6 : 1); // hysteresis: a soft syllable keeps the turn
    if (!this.speaking) {
      this.above = loud ? (this.above ?? now) : null;
      if (this.above !== null && now - this.above >= (playback ? this.bargeMs : this.startMs)) {
        this.speaking = true; this.started = this.above; this.below = null; this.voicedMs = now - this.above;
        return "start";
      }
      return null;
    }
    if (loud) this.voicedMs += dt;
    this.below = loud ? null : (this.below ?? now);
    if (now - this.started >= this.maxMs || (this.below !== null && now - this.below >= this.endMs)) {
      const voiced = this.voicedMs;
      this.reset();
      this.lastVoicedMs = voiced;
      return "end";
    }
    return null;
  }
}
if (typeof module !== "undefined") module.exports = VoiceActivity;
if (typeof window !== "undefined") window.KenobiVoiceActivity = VoiceActivity;
