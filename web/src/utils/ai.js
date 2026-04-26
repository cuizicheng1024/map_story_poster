import { EXTRACT_NAMES_PROMPT, STORY_SYSTEM_PROMPT } from './prompts';

// Get API Endpoint consistent with App.jsx logic
const getApiEndpoint = () => {
  const globalBase = window.MAP_STORY_AI_ENDPOINT || window.MAP_STORY_API_BASE;
  if (typeof globalBase === "string" && globalBase.trim()) {
    return globalBase.replace(/\/+$/, "");
  }
  if (import.meta.env.VITE_API_ENDPOINT) {
    return import.meta.env.VITE_API_ENDPOINT.replace(/\/+$/, "");
  }
  const origin = window.location.origin;
  const hostname = window.location.hostname;
  if (origin && origin !== "null" && window.location.protocol !== "file:") {
    if (hostname !== "localhost" && hostname !== "127.0.0.1") {
      return origin;
    }
  }
  // Default to Local Backend Proxy
  return "http://localhost:8765/api/ai/proxy";
  // Backup: Gemini API proxy
  // return "https://gapp.so/api/ai/gemini";
};

const API_ENDPOINT = getApiEndpoint();

async function callLLM(messages, temperature = 0.1) {
  try {
    const resp = await fetch(API_ENDPOINT, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        messages,
        temperature,
        stream: false
      })
    });

    if (!resp.ok) {
      throw new Error(`API Error: ${resp.status}`);
    }

    const data = await resp.json();
    
    // Handle different response formats (OpenAI vs others)
    if (data.choices && data.choices.length > 0) {
      return data.choices[0].message?.content || "";
    }
    if (data.content) return data.content;
    if (data.text) return data.text;
    
    return "";
  } catch (error) {
    console.error("LLM Call Failed:", error);
    return null;
  }
}

export async function extractHistoricalFigures(text) {
  const messages = [
    { role: "system", content: EXTRACT_NAMES_PROMPT },
    { role: "user", content: text }
  ];
  
  const result = await callLLM(messages, 0);
  if (!result) return [];
  
  try {
    // Try to find JSON array in the response
    const match = result.match(/\[.*\]/s);
    if (match) {
      const jsonStr = match[0];
      const data = JSON.parse(jsonStr);
      if (Array.isArray(data)) {
        return data.filter(item => typeof item === 'string' && item.trim());
      }
    }
    // Fallback if no JSON found
    const clean = result.trim();
    return clean ? [clean] : [];
  } catch (e) {
    console.warn("Failed to parse extracted names:", e);
    return [];
  }
}

export async function generateHistoricalMarkdown(person, onProgress) {
  const messages = [
    { role: "system", content: STORY_SYSTEM_PROMPT },
    { role: "user", content: `请整理历史人物「${person}」的生平信息，并按要求输出。` }
  ];
  
  if (onProgress) onProgress("正在生成生平传记...");
  const content = await callLLM(messages, 0.1);
  return content;
}
