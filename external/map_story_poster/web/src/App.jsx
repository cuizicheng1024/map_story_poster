import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { extractHistoricalFigures, generateHistoricalMarkdown } from './utils/ai';
import { parseMarkdown } from './utils/markdownParser';
import { geocodeCity } from './utils/geocoder';
import StoryMap from './components/StoryMap';
import HomeGraph from './components/HomeGraph';
import peopleNames from './data/pep_people_merged.json';

const MAX_INPUT_LEN = 200;
const historyItems = ["曹操", "李白", "苏轼", "康熙", "唐三藏"];

export default function App() {
  const [messages, setMessages] = useState([
    {
      id: crypto.randomUUID(),
      type: "text",
      role: "assistant",
      text: "输入历史人物名称，我会检索相关事件并生成人物简介和足迹地图。"
    }
  ]);
  const [inputValue, setInputValue] = useState("");
  const [homeSearch, setHomeSearch] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const chatEndRef = useRef(null);

  const quickResults = useMemo(() => {
    const q = homeSearch.trim();
    if (!q) return peopleNames.slice(0, 10);
    return peopleNames.filter((x) => String(x).includes(q)).slice(0, 10);
  }, [homeSearch]);

  useEffect(() => {
    if (chatEndRef.current) {
      chatEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages]);

  const appendMessage = useCallback((payload) => {
    setMessages((prev) => [...prev, { id: crypto.randomUUID(), ...payload }]);
  }, []);

  const handleGenerate = async (text) => {
    if (!text.trim()) return;
    if (isLoading) return;
    
    setIsLoading(true);
    appendMessage({ type: "text", role: "user", text });
    
    try {
      // 1. Extract Name
      const figures = await extractHistoricalFigures(text);
      if (figures.length === 0) {
        appendMessage({ type: "text", role: "assistant", text: "未能识别出具体的历史人物，请重试。" });
        setIsLoading(false);
        return;
      }
      
      const person = figures[0];
      appendMessage({ type: "text", role: "assistant", text: `正在生成「${person}」的生平足迹...` });
      
      // 2. Generate Markdown
      const markdown = await generateHistoricalMarkdown(person);
      if (!markdown) {
        appendMessage({ type: "text", role: "assistant", text: "生成内容失败，请稍后重试。" });
        setIsLoading(false);
        return;
      }
      
      // 3. Parse Markdown
      const data = parseMarkdown(markdown);
      if (!data.locations || data.locations.length === 0) {
        appendMessage({ type: "text", role: "assistant", text: "未能提取到足够的地点信息。" });
        setIsLoading(false);
        return;
      }
      
      // 4. Geocode Locations
      const geocodedLocations = [];
      // Show progress?
      // appendMessage({ type: "text", role: "assistant", text: `正在定位 ${data.locations.length} 个地点...` });
      
      for (const loc of data.locations) {
        const queryName = loc.locationDesc || loc.name;
        // Clean up name for geocoding (remove parenthesis, ancient names etc)
        // Simple heuristic: take text before parenthesis, or if "古称", take part after "今"
        let geoName = queryName;
        if (geoName.includes("今")) {
           const match = geoName.match(/今([^）)]+)/);
           if (match) geoName = match[1];
        }
        geoName = geoName.replace(/[（(].*?[）)]/g, "").trim();
        if (!geoName) geoName = loc.name;

        const coords = await geocodeCity(geoName);
        if (coords) {
          geocodedLocations.push({ ...loc, ...coords });
        } else {
          // Fallback: try just the name
          if (geoName !== loc.name) {
             const coords2 = await geocodeCity(loc.name);
             if (coords2) geocodedLocations.push({ ...loc, ...coords2 });
          }
        }
      }

      if (geocodedLocations.length === 0) {
        appendMessage({ type: "text", role: "assistant", text: "无法获取地点的地理坐标，无法生成地图。" });
      } else {
        appendMessage({ 
          type: "map", 
          role: "assistant", 
          person: person,
          locations: geocodedLocations,
          intro: data.intro
        });
      }

    } catch (e) {
      console.error(e);
      appendMessage({ type: "text", role: "assistant", text: `发生错误: ${e.message}` });
    } finally {
      setIsLoading(false);
    }
  };

  const onSend = () => {
    handleGenerate(inputValue);
    setInputValue("");
  };

  const onHistoryClick = (item) => {
    handleGenerate(item);
  };

  return (
    <div className="flex flex-col h-screen bg-gray-50">
      {/* Header */}
      <header className="flex-none px-6 py-4 bg-white border-b shadow-sm z-10">
        <h1 className="text-xl font-bold text-gray-800 flex items-center gap-2">
          🗺️ StoryMap <span className="text-xs font-normal text-gray-500 bg-gray-100 px-2 py-0.5 rounded-full">从空间视角重新发现历史人物生命轨迹</span>
        </h1>
      </header>

      {/* Chat Area */}
      <main className="flex-1 overflow-y-auto p-4 custom-scrollbar">
        <div className="max-w-3xl mx-auto space-y-6">
          <section className="bg-white border border-amber-100 rounded-xl p-4 shadow-sm">
            <h2 className="text-lg font-bold text-gray-800 mb-2">首页入口：搜索 + 人物知识图谱</h2>
            <div className="flex gap-2 mb-3">
              <input
                type="text"
                value={homeSearch}
                onChange={(e) => setHomeSearch(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && quickResults.length) handleGenerate(quickResults[0]);
                }}
                placeholder="搜索人教版人物（例如：吴道子、玄奘、文天祥）"
                className="flex-1 px-3 py-2 rounded-lg border border-gray-200 focus:outline-none focus:ring-2 focus:ring-amber-400"
              />
              <button
                onClick={() => quickResults.length && handleGenerate(quickResults[0])}
                disabled={!quickResults.length || isLoading}
                className="px-4 py-2 rounded-lg bg-amber-500 text-white disabled:opacity-50"
              >
                生成地图
              </button>
            </div>
            <div className="flex flex-wrap gap-2 mb-3">
              {quickResults.map((name) => (
                <button
                  key={name}
                  onClick={() => handleGenerate(name)}
                  disabled={isLoading}
                  className="px-2 py-1 text-xs rounded-full bg-amber-100 text-amber-800 hover:bg-amber-200 disabled:opacity-50"
                >
                  {name}
                </button>
              ))}
            </div>
            <HomeGraph names={peopleNames} query={homeSearch} onSelect={handleGenerate} />
          </section>

          {messages.map((msg) => (
            <div key={msg.id} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
              <div className={`max-w-[90%] md:max-w-[80%] rounded-2xl p-4 shadow-sm ${
                msg.role === "user" 
                  ? "bg-blue-600 text-white rounded-tr-sm" 
                  : "bg-white border border-gray-100 rounded-tl-sm"
              }`}>
                {msg.type === "text" && (
                  <p className="whitespace-pre-wrap leading-relaxed">{msg.text}</p>
                )}
                
                {msg.type === "map" && (
                  <div className="space-y-3">
                    <div className="flex items-baseline gap-2 border-b pb-2 mb-2">
                      <h2 className="text-lg font-bold text-gray-900">{msg.person}</h2>
                      <span className="text-xs text-gray-500">共 {msg.locations.length} 个足迹点</span>
                    </div>
                    {msg.intro && <p className="text-sm text-gray-600 mb-3">{msg.intro}</p>}
                    <div className="w-full h-[400px] rounded-lg overflow-hidden border border-gray-200 relative">
                       <StoryMap locations={msg.locations} />
                    </div>
                  </div>
                )}
              </div>
            </div>
          ))}
          {isLoading && (
             <div className="flex justify-start">
               <div className="bg-white border border-gray-100 rounded-2xl rounded-tl-sm p-4 shadow-sm">
                 <div className="flex space-x-2">
                   <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0s' }}></div>
                   <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0.2s' }}></div>
                   <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0.4s' }}></div>
                 </div>
               </div>
             </div>
          )}
          <div ref={chatEndRef} />
        </div>
      </main>

      {/* Input Area */}
      <footer className="flex-none bg-white border-t p-4">
        <div className="max-w-3xl mx-auto space-y-4">
          {/* History Chips */}
          <div className="flex flex-wrap gap-2">
            {historyItems.map((item) => (
              <button
                key={item}
                onClick={() => onHistoryClick(item)}
                disabled={isLoading}
                className="px-3 py-1 text-sm bg-gray-100 hover:bg-gray-200 text-gray-700 rounded-full transition-colors disabled:opacity-50"
              >
                {item}
              </button>
            ))}
          </div>
          
          {/* Input Box */}
          <div className="relative">
            <input
              type="text"
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value.slice(0, MAX_INPUT_LEN))}
              onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && onSend()}
              placeholder="输入历史人物名称..."
              disabled={isLoading}
              className="w-full pl-4 pr-12 py-3 bg-gray-50 border border-gray-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-blue-500 focus:bg-white transition-all disabled:opacity-60"
            />
            <button
              onClick={onSend}
              disabled={!inputValue.trim() || isLoading}
              className="absolute right-2 top-1/2 -translate-y-1/2 p-2 text-blue-600 hover:bg-blue-50 rounded-lg disabled:text-gray-400 disabled:hover:bg-transparent transition-colors"
            >
              <svg className="w-5 h-5 rotate-90" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
              </svg>
            </button>
          </div>
          <div className="text-center text-xs text-gray-400">
            StoryMap V1.0 • Web Powered by Qveris & OpenStreetMap
          </div>
        </div>
      </footer>
    </div>
  );
}
