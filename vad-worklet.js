// Niveau du micro et trames audio pour la détection de la parole (vad.js).
// Mesure RMS du micro hors du fil d'animation : les onglets en arrière-plan suspendent requestAnimationFrame ;
// l'AudioWorklet suit le moteur audio. Un message toutes les quarante millisecondes environ, consommé par call.js.
// Le même message porte `pcm`, les échantillons Float32 de la fenêtre : call.js en fait la phrase envoyée au serveur.
// Aucun son n'est conservé dans la page.
//
// Une entrée muette arrive parfois sans aucun canal (source inactive) : elle compte comme du silence, pour que la
// fin d'une phrase soit toujours vue.
class VadMeter extends AudioWorkletProcessor {
  constructor() {
    super();
    this.last = 0;
    this.acc = 0;
    this.count = 0;
    this.samples = [];
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel && channel.length) {
      for (let i = 0; i < channel.length; i += 1) {
        this.acc += channel[i] * channel[i];
      }
      this.count += channel.length;
      this.samples.push(new Float32Array(channel));
    } else {
      this.count += 128;
      this.samples.push(new Float32Array(128));
    }
    const now = currentTime * 1000;
    if (now - this.last >= 40) {
      const rms = Math.sqrt(this.acc / Math.max(1, this.count));
      const total = this.samples.reduce((size, block) => size + block.length, 0);
      const pcm = new Float32Array(total);
      let offset = 0;
      for (const block of this.samples) { pcm.set(block, offset); offset += block.length; }
      this.port.postMessage({ rms, ms: now, pcm });
      this.last = now;
      this.acc = 0;
      this.count = 0;
      this.samples = [];
    }
    return true;
  }
}

registerProcessor('vad-meter', VadMeter);
