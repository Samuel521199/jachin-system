/**
 * Voice Test Component - 语音测试组件
 * 
 * 提供语音识别、语音合成和语音聊天的测试界面
 */

import { useState, useRef } from 'react';
import { BACKEND_URL } from '../lib/api';

const API_BASE = `${BACKEND_URL}/api/v2/voice`;

interface VoiceTestProps {
  className?: string;
}

export default function VoiceTest({ className }: VoiceTestProps) {
  const [sttText, setSttText] = useState('');
  const [chatText, setChatText] = useState('');
  const [status, setStatus] = useState<{ type: 'info' | 'success' | 'error'; message: string } | null>(null);
  const [isRecording, setIsRecording] = useState(false);
  const [recordingType, setRecordingType] = useState<'stt' | 'chat' | null>(null);
  const [ttsText, setTtsText] = useState('你好，我是Jachin助手');
  const [selectedVoice, setSelectedVoice] = useState('zh-CN-XiaoxiaoNeural');
  
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const chatAudioRef = useRef<HTMLAudioElement | null>(null);

  const showStatus = (message: string, type: 'info' | 'success' | 'error' = 'info') => {
    setStatus({ type, message });
    setTimeout(() => setStatus(null), 5000);
  };

  const startRecording = async (type: 'stt' | 'chat') => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mediaRecorder = new MediaRecorder(stream);
      mediaRecorderRef.current = mediaRecorder;
      audioChunksRef.current = [];
      setRecordingType(type);
      setIsRecording(true);

      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          audioChunksRef.current.push(event.data);
        }
      };

      mediaRecorder.onstop = () => {
        const audioBlob = new Blob(audioChunksRef.current, { type: 'audio/wav' });
        // 保存到状态，供后续使用
        (window as any)[`${type}AudioBlob`] = audioBlob;
        stream.getTracks().forEach(track => track.stop());
        setIsRecording(false);
        showStatus('录音完成！', 'success');
      };

      mediaRecorder.start();
      showStatus('正在录音...', 'info');
    } catch (error: any) {
      showStatus(`无法访问麦克风: ${error.message}`, 'error');
      setIsRecording(false);
    }
  };

  const stopRecording = () => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      mediaRecorderRef.current.stop();
    }
  };

  const recognizeAudio = async () => {
    const audioBlob = (window as any).sttAudioBlob;
    if (!audioBlob) {
      showStatus('请先录制音频', 'error');
      return;
    }

    showStatus('正在识别...', 'info');

    try {
      const formData = new FormData();
      formData.append('audio_file', audioBlob, 'recording.wav');
      formData.append('format', 'wav');
      formData.append('language', 'zh-CN');

      const response = await fetch(`${API_BASE}/recognize`, {
        method: 'POST',
        body: formData,
      });

      if (!response.ok) {
        const error = await response.text();
        throw new Error(error);
      }

      const result = await response.json();
      setSttText(result.text);
      showStatus('识别成功！', 'success');
    } catch (error: any) {
      showStatus(`识别失败: ${error.message}`, 'error');
    }
  };

  const synthesizeSpeech = async () => {
    if (!ttsText.trim()) {
      showStatus('请输入要合成的文本', 'error');
      return;
    }

    showStatus('正在合成...', 'info');

    try {
      const response = await fetch(`${API_BASE}/synthesize`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          text: ttsText,
          voice: selectedVoice,
          language: 'zh-CN',
          speed: 1.0,
          pitch: 1.0,
        }),
      });

      if (!response.ok) {
        const error = await response.text();
        throw new Error(error);
      }

      const audioBlob = await response.blob();
      const audioUrl = URL.createObjectURL(audioBlob);
      
      if (audioRef.current) {
        audioRef.current.src = audioUrl;
      }
      
      showStatus('合成成功！', 'success');
    } catch (error: any) {
      showStatus(`合成失败: ${error.message}`, 'error');
    }
  };

  const voiceChat = async () => {
    const audioBlob = (window as any).chatAudioBlob;
    if (!audioBlob) {
      showStatus('请先录制音频', 'error');
      return;
    }

    showStatus('正在处理（识别→LLM→合成）...', 'info');

    try {
      const formData = new FormData();
      formData.append('audio_file', audioBlob, 'recording.wav');
      formData.append('format', 'wav');
      formData.append('language', 'zh-CN');
      formData.append('return_audio', 'true');
      formData.append('voice', 'zh-CN-XiaoxiaoNeural');
      formData.append('speed', '1.0');
      formData.append('pitch', '1.0');

      const response = await fetch(`${API_BASE}/chat`, {
        method: 'POST',
        body: formData,
      });

      if (!response.ok) {
        const error = await response.text();
        throw new Error(error);
      }

      const result = await response.json();
      setChatText(result.text);

      if (result.audio_base64 && chatAudioRef.current) {
        const audioBytes = Uint8Array.from(atob(result.audio_base64), c => c.charCodeAt(0));
        const audioBlob = new Blob([audioBytes], { type: 'audio/wav' });
        const audioUrl = URL.createObjectURL(audioBlob);
        chatAudioRef.current.src = audioUrl;
      }

      showStatus('语音聊天完成！', 'success');
    } catch (error: any) {
      showStatus(`聊天失败: ${error.message}`, 'error');
    }
  };

  return (
    <div className={`p-6 space-y-6 ${className || ''}`}>
      <h2 className="text-2xl font-bold mb-4">🎤 语音功能测试</h2>

      {/* 状态提示 */}
      {status && (
        <div className={`p-3 rounded-lg ${
          status.type === 'success' ? 'bg-green-100 text-green-800' :
          status.type === 'error' ? 'bg-red-100 text-red-800' :
          'bg-blue-100 text-blue-800'
        }`}>
          {status.message}
        </div>
      )}

      {/* 语音识别 */}
      <div className="bg-gray-50 p-4 rounded-lg">
        <h3 className="text-lg font-semibold mb-3">1. 语音识别 (STT)</h3>
        <div className="flex gap-2 mb-3">
          <button
            onClick={() => startRecording('stt')}
            disabled={isRecording}
            className={`px-4 py-2 rounded ${
              isRecording && recordingType === 'stt'
                ? 'bg-red-500 text-white animate-pulse'
                : 'bg-blue-500 text-white hover:bg-blue-600'
            } disabled:opacity-50`}
          >
            🎤 {isRecording && recordingType === 'stt' ? '录音中...' : '开始录音'}
          </button>
          <button
            onClick={stopRecording}
            disabled={!isRecording || recordingType !== 'stt'}
            className="px-4 py-2 rounded bg-gray-500 text-white hover:bg-gray-600 disabled:opacity-50"
          >
            ⏹ 停止
          </button>
          <button
            onClick={recognizeAudio}
            className="px-4 py-2 rounded bg-green-500 text-white hover:bg-green-600"
          >
            🔍 识别
          </button>
        </div>
        {sttText && (
          <div className="mt-3 p-3 bg-white rounded border">
            <div className="font-semibold mb-1">识别结果：</div>
            <div>{sttText}</div>
          </div>
        )}
      </div>

      {/* 语音合成 */}
      <div className="bg-gray-50 p-4 rounded-lg">
        <h3 className="text-lg font-semibold mb-3">2. 语音合成 (TTS)</h3>
        <div className="mb-3">
          <input
            type="text"
            value={ttsText}
            onChange={(e) => setTtsText(e.target.value)}
            placeholder="输入要合成的文本..."
            className="w-full px-3 py-2 border rounded mb-2"
          />
          <select
            value={selectedVoice}
            onChange={(e) => setSelectedVoice(e.target.value)}
            className="w-full px-3 py-2 border rounded"
          >
            <option value="zh-CN-XiaoxiaoNeural">晓晓（女声）</option>
            <option value="zh-CN-YunyangNeural">云扬（男声）</option>
            <option value="zh-CN-YunxiNeural">云希（男声）</option>
          </select>
        </div>
        <button
          onClick={synthesizeSpeech}
          className="px-4 py-2 rounded bg-blue-500 text-white hover:bg-blue-600"
        >
          🔊 合成语音
        </button>
        <audio ref={audioRef} controls className="w-full mt-3" />
      </div>

      {/* 语音聊天 */}
      <div className="bg-gray-50 p-4 rounded-lg">
        <h3 className="text-lg font-semibold mb-3">3. 语音聊天（完整流程）</h3>
        <div className="flex gap-2 mb-3">
          <button
            onClick={() => startRecording('chat')}
            disabled={isRecording}
            className={`px-4 py-2 rounded ${
              isRecording && recordingType === 'chat'
                ? 'bg-red-500 text-white animate-pulse'
                : 'bg-blue-500 text-white hover:bg-blue-600'
            } disabled:opacity-50`}
          >
            🎤 {isRecording && recordingType === 'chat' ? '录音中...' : '开始录音'}
          </button>
          <button
            onClick={stopRecording}
            disabled={!isRecording || recordingType !== 'chat'}
            className="px-4 py-2 rounded bg-gray-500 text-white hover:bg-gray-600 disabled:opacity-50"
          >
            ⏹ 停止
          </button>
          <button
            onClick={voiceChat}
            className="px-4 py-2 rounded bg-green-500 text-white hover:bg-green-600"
          >
            💬 语音聊天
          </button>
        </div>
        {chatText && (
          <div className="mt-3 p-3 bg-white rounded border mb-3">
            <div className="font-semibold mb-1">AI回复：</div>
            <div>{chatText}</div>
          </div>
        )}
        <audio ref={chatAudioRef} controls className="w-full" />
      </div>
    </div>
  );
}
