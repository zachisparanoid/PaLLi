export function int16ToBase64(samples: Int16Array): string {
  const bytes = new Uint8Array(samples.buffer);
  let binary = "";
  for (let index = 0; index < bytes.length; index += 1) {
    binary += String.fromCharCode(bytes[index]);
  }
  return btoa(binary);
}

export function base64ToBytes(base64: string): Uint8Array {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

export function float32ToInt16(samples: Float32Array): Int16Array {
  const result = new Int16Array(samples.length);
  for (let index = 0; index < samples.length; index += 1) {
    const sample = Math.max(-1, Math.min(1, samples[index]));
    result[index] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
  }
  return result;
}

export function resampleFloat32(
  input: Float32Array,
  fromSampleRate: number,
  toSampleRate: number,
): Float32Array {
  if (fromSampleRate === toSampleRate) {
    return input;
  }
  const ratio = fromSampleRate / toSampleRate;
  const length = Math.round(input.length / ratio);
  const output = new Float32Array(length);
  let offset = 0;
  for (let index = 0; index < length; index += 1) {
    const nextOffset = Math.round((index + 1) * ratio);
    let total = 0;
    let count = 0;
    for (let sourceIndex = offset; sourceIndex < nextOffset && sourceIndex < input.length; sourceIndex += 1) {
      total += input[sourceIndex];
      count += 1;
    }
    output[index] = count > 0 ? total / count : 0;
    offset = nextOffset;
  }
  return output;
}

type QueuedClip = {
  url: string;
  mimeType: string;
};

export class BrowserAudioQueue {
  private queue: QueuedClip[] = [];
  private current: HTMLAudioElement | null = null;
  private closed = false;

  async enqueueBase64(base64: string, mimeType = "audio/wav"): Promise<void> {
    if (this.closed) return;
    const bytes = base64ToBytes(base64);
    const copied = new Uint8Array(bytes.byteLength);
    copied.set(bytes);
    const arrayBuffer = copied.buffer as ArrayBuffer;
    const blob = new Blob([arrayBuffer], { type: mimeType });
    const url = URL.createObjectURL(blob);
    this.queue.push({ url, mimeType });
    if (!this.current) {
      await this.playNext();
    }
  }

  interrupt(): void {
    if (this.current) {
      this.current.pause();
      URL.revokeObjectURL(this.current.src);
      this.current.src = "";
      this.current = null;
    }
    for (const item of this.queue) {
      URL.revokeObjectURL(item.url);
    }
    this.queue = [];
  }

  async close(): Promise<void> {
    this.closed = true;
    this.interrupt();
  }

  private async playNext(): Promise<void> {
    if (this.closed || this.current || this.queue.length === 0) return;
    const next = this.queue.shift();
    if (!next) return;

    const audio = new Audio(next.url);
    this.current = audio;
    audio.autoplay = true;
    audio.onended = () => {
      URL.revokeObjectURL(next.url);
      this.current = null;
      void this.playNext();
    };
    audio.onerror = () => {
      URL.revokeObjectURL(next.url);
      this.current = null;
      void this.playNext();
    };
    try {
      await audio.play();
    } catch {
      URL.revokeObjectURL(next.url);
      this.current = null;
    }
  }
}
