// Helper to extract key-value pairs from lines like "- **Key**: Value"
const extractField = (line, keyPattern) => {
  const regex = new RegExp(`^-\\s*\\*\\*(${keyPattern})\\*\\*：\\s*(.+)`);
  const match = line.match(regex);
  if (match) return match[2].trim();
  return null;
};

// Helper to extract section content
const extractSection = (text, headerPattern) => {
  const lines = text.split('\n');
  let inSection = false;
  const content = [];
  
  for (const line of lines) {
    if (line.match(/^##\s+/)) {
      if (line.match(headerPattern)) {
        inSection = true;
        continue;
      } else if (inSection) {
        break;
      }
    }
    if (inSection) content.push(line);
  }
  return content.join('\n');
};

export const parseMarkdown = (markdown) => {
  const result = {
    person: {},
    locations: [],
    timeline: [],
    intro: ""
  };
  
  if (!markdown) return result;

  // 1. Parse Person Profile (Section 1)
  const profileSection = extractSection(markdown, /人物档案|基本信息/);
  if (profileSection) {
    const lines = profileSection.split('\n');
    let currentKey = "";
    
    for (const line of lines) {
      // Basic Info
      if (line.includes("**姓名**")) result.person.name = extractField(line, "姓名");
      if (line.includes("**时代**")) result.person.era = extractField(line, "时代|朝代");
      if (line.includes("**出生**")) result.person.birth = extractField(line, "出生");
      if (line.includes("**去世**")) result.person.death = extractField(line, "去世");
      if (line.includes("**享年**")) result.person.age = extractField(line, "享年");
      if (line.includes("**主要身份**")) result.person.roles = extractField(line, "主要身份");
      
      // Overview
      if (line.match(/^###\s+生平概述/)) {
        currentKey = "overview";
        continue;
      }
      if (currentKey === "overview" && line.trim() && !line.startsWith("#")) {
        result.intro += line.trim() + " ";
      }
    }
  }

  // 2. Parse Locations (Section 3)
  const locationSection = extractSection(markdown, /人生历程|重要地点/);
  if (locationSection) {
    const lines = locationSection.split('\n');
    let currentLocation = null;
    
    for (const line of lines) {
      // Header level 3 defines a location
      const headerMatch = line.match(/^###\s+[🟢🔴📍]?\s*(.+)/);
      if (headerMatch) {
        if (currentLocation) result.locations.push(currentLocation);
        
        let name = headerMatch[1].trim();
        let type = "normal";
        
        if (name.includes("出生地")) {
          type = "birth";
          name = name.replace(/出生地[：:]\s*/, "");
        } else if (name.includes("去世地")) {
          type = "death";
          name = name.replace(/去世地[：:]\s*/, "");
        } else if (name.includes("重要地点")) {
          name = name.replace(/重要地点[：:]\s*/, "");
        }
        
        // Remove emoji if present
        name = name.replace(/^[\u{1F300}-\u{1F9FF}]/u, "").trim();
        
        currentLocation = {
          name,
          type,
          time: "",
          desc: "",
          significance: "",
          quotes: []
        };
        continue;
      }
      
      if (currentLocation) {
        const time = extractField(line, "公元纪年|时间|时段");
        if (time) currentLocation.time = time;
        
        const event = extractField(line, "事迹|经过|事件");
        if (event) currentLocation.desc = event;
        
        const significance = extractField(line, "意义|影响");
        if (significance) currentLocation.significance = significance;
        
        const quotes = extractField(line, "名篇名句|代表名句|名句");
        if (quotes) currentLocation.quotes = quotes.split(/[；;]/).map(s => s.trim()).filter(Boolean);
        
        // Also capture location description (ancient/modern name)
        const locDesc = extractField(line, "位置|地点");
        if (locDesc) currentLocation.locationDesc = locDesc;
      }
    }
    // Push the last one
    if (currentLocation) result.locations.push(currentLocation);
  }

  // 3. Parse Timeline (Section 4)
  const timelineSection = extractSection(markdown, /生平时间线/);
  if (timelineSection) {
    const lines = timelineSection.split('\n');
    let tableStarted = false;
    
    for (const line of lines) {
      if (line.trim().startsWith("|") && line.includes("年份")) {
        tableStarted = true;
        continue; // Header
      }
      if (tableStarted && line.trim().startsWith("|") && !line.includes("---")) {
        const parts = line.split('|').map(s => s.trim()).filter(s => s);
        if (parts.length >= 3) {
          result.timeline.push({
            year: parts[0],
            age: parts[1],
            event: parts[2]
          });
        }
      }
    }
  }

  return result;
};
