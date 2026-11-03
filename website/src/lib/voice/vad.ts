import { float32ToInt16, resampleFloat32 } from "@/lib/voice/audio";

type VadOptions = {
  speechThreshold: number;
  silenceMs: number;
  prerollMs: number;
  targetSampleRate?: number;
  onLevel: (level: number) => void;
  onSpeechStart: () => void;
  onSpeechEnd: (
    pcm16: Int16Array,
    sampleRate: number,
    speechMetrics: {
      speech_rms: number;
      speech_peak: number;
      speech_samples: number;
      speech_duration_seconds: number;
    },
  ) => void;
};

export class BrowserVadRecorder {
  private options: VadOptions;
  private stream: MediaStream | null = null;
  private context: AudioContext | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private processor: ScriptProcessorNode | null = null;
  private sink: GainNode | null = null;
  private speechFrames: Float32Array[] = [];
  private speechRmsSquares: number[] = [];
  private speechPeaks: number[] = [];
  private speechSampleCount = 0;
  private speechActive = false;
  private silenceAccumMs = 0;
  private sampleRate = 48000;
  private muted = false;
  private captureEnabled = false;
  private prerollFrames: Float32Array[] = [];
  private prerollMs = 0;

  constructor(options: VadOptions) {
    this.options = options;
  }

  async start(): Promise<void> {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
    this.context = new AudioContext();
    this.sampleRate = this.context.sampleRate;
    this.source = this.context.createMediaStreamSource(this.stream);
    this.processor = this.context.createScriptProcessor(2048, 1, 1);
    this.sink = this.context.createGain();
    this.sink.gain.value = 0;
    this.processor.onaudioprocess = (event) => {
      const input = new Float32Array(event.inputBuffer.getChannelData(0));
      this.handleFrame(input, event.inputBuffer.sampleRate);
    };
    this.source.connect(this.processor);
    this.processor.connect(this.sink);
    this.sink.connect(this.context.destination);
    await this.context.resume();
  }

  setMuted(value: boolean): void {
    this.muted = value;
    if (value) {
      this.resetSpeechState();
    }
  }

  setCaptureEnabled(value: boolean): void {
    this.captureEnabled = value;
    if (!value) {
      this.resetSpeechState();
    }
  }

  flush(): void {
    if (!this.speechActive || this.speechFrames.length === 0) {
      this.resetSpeechState();
      return;
    }
    this.finalizeSpeech(this.sampleRate);
  }

  async stop(): Promise<void> {
    this.resetSpeechState();
    this.processor?.disconnect();
    this.source?.disconnect();
    this.sink?.disconnect();
    this.stream?.getTracks().forEach((track) => track.stop());
    if (this.context !== null) {
      await this.context.close();
    }
    this.stream = null;
    this.context = null;
    this.source = null;
    this.processor = null;
    this.sink = null;
  }

  private handleFrame(frame: Float32Array, sampleRate: number): void {
    if (this.muted) {
      this.options.onLevel(0);
      return;
    }

    const rms = computeRms(frame);
    const peak = computePeak(frame);
    const frameMs = (frame.length / sampleRate) * 1000;
    this.options.onLevel(rms);

    if (!this.captureEnabled) {
      return;
    }

    if (rms >= this.options.speechThreshold) {
      if (!this.speechActive) {
        this.speechActive = true;
        this.speechFrames = [...this.prerollFrames];
        this.options.onSpeechStart();
      }
      this.speechFrames.push(frame);
      this.speechRmsSquares.push(rms * rms * frame.length);
      this.speechPeaks.push(peak);
      this.speechSampleCount += frame.length;
      this.silenceAccumMs = 0;
      return;
    }

    if (this.speechActive) {
      this.speechFrames.push(frame);
      this.speechRmsSquares.push(rms * rms * frame.length);
      this.speechPeaks.push(peak);
      this.speechSampleCount += frame.length;
      this.silenceAccumMs += frameMs;
      if (this.silenceAccumMs >= this.options.silenceMs) {
        this.finalizeSpeech(sampleRate);
      }
      return;
    }

    this.pushPreroll(frame, frameMs);
  }

  private finalizeSpeech(sampleRate: number): void {
    const merged = concatFloat32(this.speechFrames);
    const speechSamples = this.speechSampleCount;
    const speechRms = speechSamples > 0
      ? Math.sqrt(this.speechRmsSquares.reduce((sum, value) => sum + value, 0) / speechSamples)
      : 0;
    const speechPeak = this.speechPeaks.length > 0 ? Math.max(...this.speechPeaks) : 0;
    const durationSeconds = merged.length / sampleRate;
    this.resetSpeechState();
    if (merged.length < sampleRate * 0.2) {
      return;
    }
    const targetSampleRate = this.options.targetSampleRate ?? 16000;
    const downsampled = resampleFloat32(merged, sampleRate, targetSampleRate);
    const pcm16 = float32ToInt16(downsampled);
    this.options.onSpeechEnd(pcm16, targetSampleRate, {
      speech_rms: speechRms * 32768,
      speech_peak: speechPeak * 32768,
      speech_samples: speechSamples,
      speech_duration_seconds: durationSeconds,
    });
  }

  private pushPreroll(frame: Float32Array, frameMs: number): void {
    this.prerollFrames.push(frame);
    this.prerollMs += frameMs;
    while (this.prerollMs > this.options.prerollMs && this.prerollFrames.length > 0) {
      const removed = this.prerollFrames.shift();
      if (removed) {
        this.prerollMs -= (removed.length / this.sampleRate) * 1000;
      }
    }
  }

  private resetSpeechState(): void {
    this.speechFrames = [];
    this.speechRmsSquares = [];
    this.speechPeaks = [];
    this.speechSampleCount = 0;
    this.prerollFrames = [];
    this.prerollMs = 0;
    this.speechActive = false;
    this.silenceAccumMs = 0;
  }
}

function computeRms(frame: Float32Array): number {
  let sum = 0;
  for (let index = 0; index < frame.length; index += 1) {
    sum += frame[index] * frame[index];
  }
  return Math.sqrt(sum / frame.length);
}

function computePeak(frame: Float32Array): number {
  let peak = 0;
  for (let index = 0; index < frame.length; index += 1) {
    peak = Math.max(peak, Math.abs(frame[index]));
  }
  return peak;
}

function concatFloat32(chunks: Float32Array[]): Float32Array {
  const totalLength = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const merged = new Float32Array(totalLength);
  let offset = 0;
  for (const chunk of chunks) {
    merged.set(chunk, offset);
    offset += chunk.length;
  }
  return merged;
}
