import { describe, expect, it } from 'vitest'
import { collectDownloadJobs, downloadCaption } from './jobs'
import { mergeDownloadJobs } from './live'
import type { VoiceStatus } from '../api/voice'

function voice(partial: Partial<VoiceStatus> = {}): VoiceStatus {
  return {
    tts: { available: false, enabled: true, engine: null, reason: null },
    stt: { available: false, enabled: true, engine: null, reason: null },
    settings: {
      tts_enabled: true,
      stt_enabled: true,
      speak_replies: false,
      voice_override: '',
      speed: 1,
      stt_model: 'small',
      language: '',
    },
    ...partial,
  }
}

describe('collectDownloadJobs', () => {
  it('is empty when nothing is downloading', () => {
    expect(collectDownloadJobs({})).toEqual([])
    expect(collectDownloadJobs({ voice: voice() })).toEqual([])
  })

  it('folds every voice piece into one job with the highest percent', () => {
    const jobs = collectDownloadJobs({
      voice: voice({
        pack: { install: { status: 'downloading', percent: 25, message: "Downloading Remedy's voice" } },
        tts: {
          available: false,
          enabled: true,
          engine: null,
          reason: null,
          install: { status: 'downloading', percent: 62 },
        },
      }),
    })
    expect(jobs).toEqual([
      { id: 'voice', label: "Downloading Remedy's voice", percent: 62 },
    ])
  })

  it('treats STT loading as indeterminate', () => {
    const jobs = collectDownloadJobs({
      voice: voice({
        stt: {
          available: false,
          enabled: true,
          engine: null,
          reason: null,
          install: { status: 'loading' },
        },
      }),
    })
    expect(jobs[0]?.id).toBe('voice')
    expect(jobs[0]?.label).toBe('Hearing')
    expect(jobs[0]?.percent).toBeNull()
  })

  it('includes vision and Hugging Face pulls', () => {
    const jobs = collectDownloadJobs({
      vision: {
        enabled: true,
        installed: false,
        running: false,
        ready: false,
        model_id: 'smol',
        progress: {
          phase: 'downloading',
          bytes_done: 400,
          bytes_total: 1000,
          message: 'SmolVLM2',
        },
      },
      hf: {
        phase: 'downloading',
        filename: 'model.gguf',
        pct: 18,
      },
    })
    expect(jobs.map((j) => j.id)).toEqual(['vision', 'hf'])
    expect(jobs[0]?.percent).toBe(40)
    expect(jobs[1]?.label).toBe('model.gguf')
    expect(downloadCaption(jobs)).toBe('SmolVLM2 40% · model.gguf 18%')
  })

  it('keeps an optimistic job until the server reports the same id', () => {
    const server = collectDownloadJobs({
      voice: voice({
        tts: {
          available: false,
          enabled: true,
          engine: null,
          reason: null,
          install: { status: 'downloading', percent: 12 },
        },
      }),
    })
    const merged = mergeDownloadJobs(server, [
      { id: 'voice', label: "Downloading Remedy's voice", percent: null },
      { id: 'hf', label: 'model.gguf', percent: 3 },
    ])
    expect(merged.map((j) => j.id)).toEqual(['voice', 'hf'])
    expect(merged[0]?.percent).toBe(12)
  })
})
