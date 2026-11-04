import fs from "node:fs";
import http from "node:http";
import type { AddressInfo } from "node:net";
import path from "node:path";
import process from "node:process";
import { spawn } from "node:child_process";
import { strToU8, zipSync } from "fflate";

const V6_FIXTURE_FILES = new Set([
  "batch-modal-real-shape.html",
  "custom-page-size.html",
  "download-out-of-order.html",
  "filter-tree.html",
  "grouped-results.html",
  "same-query-rerun.html",
  "same-url-slow-search.html",
  "selector-flicker.html",
  "selector-missing.html",
  "stale-page5.html",
  "search.html",
  "search-missing-submit.html",
  "results.html",
  "multipage.html",
  "detail.html",
  "detail-fulltext-clauses.html"
]);

const LOGIN_HTML = `<!doctype html>
<html lang="zh-CN" data-pkulaw-profile="fixture">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>本地测试机构 · 登录</title>
  <style>
    body{margin:0;min-height:100vh;display:grid;place-items:center;background:#edf1f4;
      font-family:"Microsoft YaHei UI",sans-serif;color:#23384d}
    .card{width:min(520px,calc(100vw - 64px));padding:38px;border-radius:22px;background:#fff;
      border:1px solid #dce2e8;box-shadow:0 22px 60px rgba(28,45,62,.12)}
    .mark{width:52px;height:52px;display:grid;place-items:center;border-radius:15px;
      background:linear-gradient(145deg,#e6bd78,#cf9848);color:#23384d;font:700 25px Georgia}
    h1{margin:24px 0 8px;font:500 31px Georgia,"Songti SC",serif}
    p{margin:0;color:#788695;font-size:13px;line-height:1.8}
    button{width:100%;height:46px;margin-top:26px;border:0;border-radius:12px;
      background:linear-gradient(135deg,#315f8f,#244a70);color:#fff;font:700 13px "Microsoft YaHei UI",sans-serif;
      cursor:pointer;box-shadow:0 10px 22px rgba(37,75,112,.2)}
    .tag{display:inline-block;margin-top:20px;padding:5px 9px;border-radius:999px;
      background:#edf5ff;color:#356da8;font-size:10px}
  </style>
</head>
<body>
  <section class="card">
    <div class="mark">试</div>
    <span class="tag">STAGE 2 · LOCAL FIXTURE</span>
    <h1>本地登录与浏览器链路测试</h1>
    <p>该页面只用于验证 Electron 内置浏览器、持久 Session、Python 连接和人工操作，不访问北大法宝。</p>
    <button id="fixture-login" type="button" onclick="location.href='/fixture-login'">模拟机构登录</button>
  </section>
</body>
</html>`;

const APP_HTML = `<!doctype html>
<html lang="zh-CN" data-auth-ready="true" data-pkulaw-profile="fixture">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>内置浏览器测试台</title>
  <style>
    *{box-sizing:border-box} body{margin:0;min-height:100vh;background:#f2f5f7;
      font-family:"Microsoft YaHei UI",sans-serif;color:#21374c}
    .top{height:64px;display:flex;align-items:center;justify-content:space-between;padding:0 28px;
      background:#fff;border-bottom:1px solid #dce2e8}
    .brand{display:flex;align-items:center;gap:11px;font-weight:700}
    .mark{width:34px;height:34px;display:grid;place-items:center;border-radius:10px;
      background:#e5bd78;color:#253b51;font:700 18px Georgia}
    .pill{padding:7px 11px;border-radius:999px;background:#e9f5ee;color:#2f7654;font-size:11px;font-weight:700}
    main{padding:30px}
    .hero{padding:26px 30px;border-radius:18px;background:linear-gradient(130deg,#233f59,#365b7a);color:#fff}
    h1{margin:0;font:500 32px Georgia,"Songti SC",serif}
    .hero p{margin:10px 0 0;color:#c5d1dc;font-size:13px}
    .grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin-top:16px}
    .card{padding:22px;border:1px solid #dce2e8;border-radius:15px;background:#fff}
    .card h2{margin:0;font-size:15px}.card p{margin:8px 0 16px;color:#7d8997;font-size:11px;line-height:1.7}
    button{height:38px;padding:0 15px;border:1px solid #d5dde5;border-radius:9px;background:#fff;
      color:#3c5369;cursor:pointer;font:600 11px "Microsoft YaHei UI",sans-serif}
    button.primary{border:0;background:#2e5f8e;color:#fff}
    #manual-status{margin-top:16px;color:#7b8896;font-size:11px}
    #captcha-overlay{position:fixed;inset:0;display:none;place-items:center;background:rgba(21,34,47,.56);
      backdrop-filter:blur(5px);z-index:20}
    #captcha-overlay .box{width:390px;padding:28px;border-radius:18px;background:#fff;text-align:center;
      box-shadow:0 24px 70px rgba(0,0,0,.28)}
    #captcha-overlay .slider{height:42px;margin-top:18px;display:grid;place-items:center;border-radius:9px;
      background:#f1f4f7;color:#657484;font-size:11px}
  </style>
</head>
<body>
  <header class="top">
    <div class="brand"><span class="mark">试</span> 本地机构服务台</div>
    <span class="pill">机构已认证</span>
  </header>
  <main>
    <section class="hero">
      <h1>Electron 内置页面已就绪</h1>
      <p>该页面由 Electron 自己承载，通过随机回环 DevTools 端点交给 Python/DrissionPage 控制。</p>
    </section>
    <div class="grid">
      <article class="card">
        <h2>人工验证状态</h2>
        <p>触发一个可见的滑块遮罩，验证程序会暂停并等待人工继续。</p>
        <button id="show-captcha" type="button">显示滑块验证</button>
        <div id="manual-status">尚未请求人工处理</div>
      </article>
      <article class="card">
        <h2>下载与子窗口</h2>
        <p>验证 Electron Session 接管下载，以及新窗口进入受控 WebContentsView。</p>
        <button id="download" class="primary" type="button">下载测试文件</button>
        <button id="open-child" type="button">打开受控子窗口</button>
      </article>
    </div>
  </main>
  <div id="captcha-overlay">
    <div class="box">
      <strong>请人工完成滑块验证</strong>
      <p>程序必须暂停，不能自动处理验证码。</p>
      <div class="slider">拖动滑块验证 →</div>
    </div>
  </div>
  <script>
    document.querySelector('#show-captcha').addEventListener('click', () => {
      document.querySelector('#captcha-overlay').style.display = 'grid';
      document.querySelector('#manual-status').textContent = '等待人工完成滑块验证';
    });
    document.querySelector('#download').addEventListener('click', () => {
      location.href = '/download';
    });
    document.querySelector('#open-child').addEventListener('click', () => {
      window.open('/child', 'controlled-child', 'width=680,height=520');
    });
  </script>
</body>
</html>`;

const CHILD_HTML = `<!doctype html>
<html lang="zh-CN" data-pkulaw-profile="fixture">
<head><meta charset="utf-8"><title>受控子窗口</title></head>
<body style="margin:0;min-height:100vh;display:grid;place-items:center;background:#fff;
  font-family:'Microsoft YaHei UI',sans-serif;color:#263d53">
  <div style="text-align:center">
    <h1 style="font-family:Georgia,'Songti SC',serif">受控子窗口</h1>
    <p style="color:#768493;font-size:13px">由 Electron 主进程接管，没有逃逸到系统浏览器。</p>
  </div>
</body>
</html>`;

interface FixturePolicy {
  id: string;
  title: string;
  library: "chl" | "lar";
  province: string;
  validity: string;
  issuingAuthority: string;
  documentNumber: string;
  publicationDate: string;
  effectiveDate: string;
  effectLevel: string;
  body: string;
  wordExtension?: ".doc" | ".docx";
}

interface GovernmentFixture {
  id: string;
  title: string;
  official: boolean;
  authority: string;
  documentNumber: string;
  publicationDate: string;
  effectiveDate: string;
  validity: string;
  effectLevel: string;
  body: string;
  attachment: boolean;
}

const GOVERNMENT_POLICIES: GovernmentFixture[] = [
  {
    id: "gov-native",
    title: "政府官网补全政策",
    official: true,
    authority: "国务院",
    documentNumber: "国发〔2024〕21号",
    publicationDate: "2024-06-18",
    effectiveDate: "2024-07-01",
    validity: "现行有效",
    effectLevel: "国务院文件",
    body: "政府官网原生附件测试正文，用于验证精确标题补全及来源标注。",
    attachment: true
  },
  {
    id: "gov-html-only",
    title: "政府官网正文整理政策",
    official: true,
    authority: "国务院办公厅",
    documentNumber: "国办发〔2023〕16号",
    publicationDate: "2023-09-08",
    effectiveDate: "2023-10-01",
    validity: "现行有效",
    effectLevel: "国务院规范性文件",
    body: "该政策只有官方 HTML 正文，没有原生 Word 附件。程序必须生成明确标注为“政府官网正文整理”的 Word，并另存 TXT。",
    attachment: false
  },
  {
    id: "gov-source-anomaly",
    title: "政府官网来源异常政策",
    official: false,
    authority: "未知转载站点",
    documentNumber: "",
    publicationDate: "2022-01-01",
    effectiveDate: "",
    validity: "",
    effectLevel: "",
    body: "这不是可接受的官方来源，不能写入正式来源。",
    attachment: false
  }
];

const POLICIES: FixturePolicy[] = [
  {
    id: "cn-stage3-001",
    title: "阶段三测试政策",
    library: "chl",
    province: "",
    validity: "现行有效",
    issuingAuthority: "国务院",
    documentNumber: "国发〔2024〕1号",
    publicationDate: "2024-03-01",
    effectiveDate: "2024-04-01",
    effectLevel: "行政法规",
    body: "这是用于验证逐篇采集、详情元数据读取和 Word 下载的本地测试政策正文。"
  },
  {
    id: "legacy-doc-stage3-001",
    title: "旧版 Word 格式测试政策",
    library: "chl",
    province: "",
    validity: "现行有效",
    issuingAuthority: "测试机关",
    documentNumber: "测发〔2026〕83号",
    publicationDate: "2026-09-01",
    effectiveDate: "2026-10-01",
    effectLevel: "部门规范性文件",
    body: "该政策使用旧版 OLE Word 文件，用于验证真实 .doc 下载和统一格式校验。",
    wordExtension: ".doc"
  },
  {
    id: "cn-duplicate-2023",
    title: "同名政策",
    library: "chl",
    province: "",
    validity: "已废止",
    issuingAuthority: "国务院办公厅",
    documentNumber: "国办发〔2023〕8号",
    publicationDate: "2023-05-10",
    effectiveDate: "2023-06-01",
    effectLevel: "国务院规范性文件",
    body: "同名政策 2023 年版本。用于验证系统不会自动选择同名结果中的第一条。"
  },
  {
    id: "cn-duplicate-2024",
    title: "同名政策",
    library: "chl",
    province: "",
    validity: "现行有效",
    issuingAuthority: "国务院",
    documentNumber: "国发〔2024〕9号",
    publicationDate: "2024-07-20",
    effectiveDate: "2024-08-01",
    effectLevel: "行政法规",
    body: "同名政策 2024 年版本。用户必须根据公布日期、文号和机关完成选择。"
  },
  {
    id: "nm-duplicate-2025",
    title: "同名政策",
    library: "lar",
    province: "内蒙古自治区",
    validity: "现行有效",
    issuingAuthority: "内蒙古自治区人民政府",
    documentNumber: "内政发〔2025〕5号",
    publicationDate: "2025-02-15",
    effectiveDate: "2025-03-01",
    effectLevel: "地方政府规章",
    body: "同名政策地方版本。用于验证中央和地方分开检索以及候选版本消歧。"
  },
  {
    id: "nm-stage3-001",
    title: "阶段三地方政策",
    library: "lar",
    province: "内蒙古自治区",
    validity: "现行有效",
    issuingAuthority: "内蒙古自治区人民政府办公厅",
    documentNumber: "内政办发〔2022〕18号",
    publicationDate: "2022-09-12",
    effectiveDate: "2022-10-01",
    effectLevel: "地方规范性文件",
    body: "用于验证地方法规、省份筛选和地方政策下载的本地测试正文。"
  },
  {
    id: "retry-stage3-001",
    title: "失败重试政策",
    library: "chl",
    province: "",
    validity: "现行有效",
    issuingAuthority: "测试机关",
    documentNumber: "测发〔2024〕12号",
    publicationDate: "2024-11-01",
    effectiveDate: "2024-12-01",
    effectLevel: "部门规范性文件",
    body: "该政策首次打开详情页时返回错误标题，第二次打开后恢复正常，用于验证失败项重试。"
  },
  {
    id: "pause-boundary-policy",
    title: "暂停边界测试政策",
    library: "chl",
    province: "",
    validity: "现行有效",
    issuingAuthority: "测试机关",
    documentNumber: "测发〔2024〕15号",
    publicationDate: "2024-11-15",
    effectiveDate: "2024-12-15",
    effectLevel: "部门规范性文件",
    body: "该政策下载响应故意延迟，用于验证 Word 下载和校验完成后才进入暂停状态。"
  },
  {
    id: "broken-word-policy",
    title: "正文提取异常政策",
    library: "chl",
    province: "",
    validity: "现行有效",
    issuingAuthority: "测试机关",
    documentNumber: "测发〔2024〕31号",
    publicationDate: "2024-12-01",
    effectiveDate: "2025-01-01",
    effectLevel: "部门规范性文件",
    body: "该政策故意提供损坏的 Word 文件，用于验证正文提取异常时进入待核验。"
  },
  ...Array.from({ length: 12 }, (_, index): FixturePolicy => {
    const year = 2020 + (index % 6);
    return {
      id: `pagination-${index + 1}`,
      title: `分页测试政策${String(index + 1).padStart(2, "0")}`,
      library: "chl",
      province: "",
      validity: index % 2 === 0 ? "现行有效" : "已被修改",
      issuingAuthority: "测试机关",
      documentNumber: `测发〔${year}〕${index + 1}号`,
      publicationDate: `${year}-06-01`,
      effectiveDate: `${year}-07-01`,
      effectLevel: "部门规范性文件",
      body: `分页测试政策第 ${index + 1} 条正文，用于验证分页和逐条断点。`
    };
  }),
  ...Array.from({ length: 60 }, (_, index): FixturePolicy => {
    const year = 2020 + (index % 6);
    return {
      id: `batch-policy-${String(index + 1).padStart(2, "0")}`,
      title: `批量测试政策${String(index + 1).padStart(2, "0")}`,
      library: "chl",
      province: "",
      validity: "现行有效",
      issuingAuthority: "批量测试机关",
      documentNumber: `批测发〔${year}〕${index + 1}号`,
      publicationDate: `${year}-08-01`,
      effectiveDate: `${year}-09-01`,
      effectLevel: "部门规范性文件",
      body: `批量测试政策第 ${index + 1} 条正文，用于验证分批目录和 ZIP。`
    };
  }),
  ...Array.from({ length: 501 }, (_, index): FixturePolicy => {
    return {
      id: `scale-projection-${String(index + 1).padStart(3, "0")}`,
      title: `规模确认测试政策${String(index + 1).padStart(3, "0")}`,
      library: "chl",
      province: "",
      validity: "现行有效",
      issuingAuthority: "规模确认测试机关",
      documentNumber: `规测发〔2018〕${index + 1}号`,
      publicationDate: "2018-10-01",
      effectiveDate: "2018-11-01",
      effectLevel: "部门规范性文件",
      body: `规模确认测试正文 ${index + 1}，用于验证批量规模投影和二次确认。`
    };
  })
];

function escapeHtml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function searchPageHtml(omitYear = false): string {
  const currentYear = new Date().getFullYear();
  const yearOptions = Array.from(
    { length: currentYear - 2000 + 1 },
    (_, index) => `<option>${currentYear - index}</option>`
  ).join("");
  const yearFilter = omitYear
    ? ""
    : `<label>公布年份
          <select name="year">
            <option>全部年份</option>${yearOptions}
          </select>
        </label>`;
  return `<!doctype html>
<html lang="zh-CN" data-auth-ready="true" data-pkulaw-profile="fixture">
<head>
  <meta charset="utf-8">
  <title>本地北大法宝检索夹具</title>
  <style>
    *{box-sizing:border-box}body{margin:0;background:#f1f4f7;color:#263c51;
      font-family:"Microsoft YaHei UI",sans-serif}
    .top{height:72px;display:flex;align-items:center;padding:0 30px;background:#fff;border-bottom:1px solid #dce2e8}
    .brand{display:flex;align-items:center;gap:10px;font-weight:700}
    .mark{width:36px;height:36px;display:grid;place-items:center;border-radius:10px;background:#e4bd79;
      color:#263b50;font:700 19px Georgia}
    main{max-width:1040px;margin:0 auto;padding:34px}
    .hero{padding:30px;border-radius:18px;background:linear-gradient(135deg,#213e59,#355a79);color:#fff}
    h1{margin:0;font:500 30px Georgia,"Songti SC",serif}.hero p{margin:8px 0 0;color:#c6d2dd;font-size:12px}
    form{margin-top:16px;padding:22px;border:1px solid #dce2e8;border-radius:16px;background:#fff}
    .search-row{display:grid;grid-template-columns:1fr 110px;gap:9px}
    input,select{height:39px;padding:0 11px;border:1px solid #d7dfe6;border-radius:9px;background:#fbfcfd;
      color:#344b60;font:12px "Microsoft YaHei UI",sans-serif}
    button{height:39px;border:0;border-radius:9px;background:#2d5e8c;color:#fff;font-weight:700;cursor:pointer}
    #subLibraries{display:flex;gap:8px;margin-top:14px}
    #subLibraries a{padding:8px 13px;border:1px solid #d8e0e7;border-radius:9px;color:#5c6f81;
      text-decoration:none;font-size:11px;cursor:pointer}
    #subLibraries a.current{background:#eaf2fa;border-color:#8fb0ce;color:#2d6699;font-weight:700}
    .filters{display:grid;grid-template-columns:repeat(4,1fr);gap:9px;margin-top:15px}
    label{display:grid;gap:5px;color:#7a8794;font-size:10px}
  </style>
</head>
<body>
  <header class="top"><div class="brand"><span class="mark">法</span>本地北大法宝检索夹具</div></header>
  <main>
    <section class="hero">
      <h1>法规检索测试台</h1>
      <p>该页面仅用于阶段 3 离线适配器验证，不访问真实北大法宝。</p>
    </section>
    <form id="pkulaw-search-form" action="/pkulaw/search" method="get">
      ${omitYear ? '<input type="hidden" name="noYear" value="1">' : ""}
      <input type="hidden" id="library-value" name="library" value="chl">
      <select name="SearchKeywordType" style="display:none">
        <option value="Title" selected>标题</option>
        <option value="FullText">全文</option>
      </select>
      <div class="search-row">
        <input id="txtSearch" name="keyword" placeholder="输入标题或关键词">
        <button id="pkulaw-search-submit" type="submit">精确检索</button>
      </div>
      <div id="subLibraries">
        <a library="chl" class="current" onclick="setLibrary('chl',this);return false">中央法规</a>
        <a library="lar" onclick="setLibrary('lar',this);return false">地方法规</a>
      </div>
      <div class="filters">
        ${yearFilter}
        <label>省份
          <select name="province">
            <option>全部省份</option><option>内蒙古自治区</option><option>北京市</option>
          </select>
        </label>
        <label>时效性
          <select name="timeliness">
            <option>全部（仅记录元数据，不筛选）</option><option>现行有效</option>
            <option>已被修改</option><option>已废止</option>
          </select>
        </label>
        <label>每页条数
          <select name="pageSize"><option>10</option><option selected>20</option><option>50</option><option>100</option></select>
        </label>
      </div>
    </form>
  </main>
  <script>
    function setLibrary(value, element) {
      document.querySelector('#library-value').value = value;
      document.querySelectorAll('#subLibraries a').forEach(item => item.classList.remove('current'));
      element.classList.add('current');
    }
  </script>
</body>
</html>`;
}

function applyPolicyFilters(
  policies: FixturePolicy[],
  params: URLSearchParams
): FixturePolicy[] {
  const library = params.get("library") || params.get("Library") || "chl";
  const year = params.get("year") || "全部年份";
  const province = params.get("province") || "全部省份";
  const timeliness = params.get("timeliness") || "全部（仅记录元数据，不筛选）";
  return policies.filter((policy) => {
    if (policy.library !== library) return false;
    if (year !== "全部年份" && !policy.publicationDate.startsWith(year)) return false;
    if (library === "lar" && province !== "全部省份" && policy.province !== province) return false;
    if (!timeliness.startsWith("全部") && policy.validity !== timeliness) return false;
    return true;
  });
}

function resultsPageHtml(params: URLSearchParams, baseUrl: string): string {
  const keyword = params.get("keyword") || "";
  const library = params.get("library") || params.get("Library") || "chl";
  const searchType = params.get("SearchKeywordType") || "Title";
  const page = Math.max(1, Number(params.get("page") || 1));
  const pageSize = Math.max(1, Number(params.get("pageSize") || 20));
  const groupMode = params.get("group") === "2" ? "2" : "0";
  const omitYear = params.get("noYear") === "1";
  const searchedAll = POLICIES.filter((policy) => policy.title.includes(keyword));
  const matched = applyPolicyFilters(searchedAll, params);
  const totalPages = Math.max(1, Math.ceil(matched.length / pageSize));
  const safePage = Math.min(page, totalPages);
  const pageRows = matched.slice((safePage - 1) * pageSize, safePage * pageSize);
  const rows = pageRows
    .map(
      (policy, index) => `<article class="item">
        <div class="col"><div class="t">
          <label class="row-sort"><span>序号</span><b name="sortNum">${(safePage - 1) * pageSize + index + 1}</b></label>
          <label class="row-check"><input type="checkbox" name="recordList" value="${policy.id}" title="${escapeHtml(policy.title)}"><span>选择</span></label>
          <h4><a
          href="/pkulaw/detail/${policy.id}.html"
          data-policy-id="${policy.id}"
          data-province="${escapeHtml(policy.province)}"
          data-library="${policy.library}"
        >${escapeHtml(policy.title)}</a></h4>
        </div></div>
        <div class="info">
          <a href="?Aggs.TimelinessDic=${encodeURIComponent(policy.validity)}">${escapeHtml(policy.validity)}</a>
          <a href="?Aggs.IssueDepartment=${encodeURIComponent(policy.issuingAuthority)}">${escapeHtml(policy.issuingAuthority)}</a>
          <span class="text">公布${escapeHtml(policy.publicationDate)}</span>
          <span class="text">施行${escapeHtml(policy.effectiveDate)}</span>
          <span class="text">${escapeHtml(policy.documentNumber)}</span>
        </div>
      </article>`
    )
    .join("");
  const empty = matched.length === 0;
  const nextParams = new URLSearchParams(params);
  nextParams.set("page", String(Math.min(totalPages, safePage + 1)));
  const prevParams = new URLSearchParams(params);
  prevParams.set("page", String(Math.max(1, safePage - 1)));
  const pagination = `页数 ${safePage} / ${totalPages}`;
  return `<!doctype html>
<html lang="zh-CN" data-auth-ready="true" data-pkulaw-profile="fixture">
<head>
  <meta charset="utf-8"><title>检索结果 · ${escapeHtml(keyword)}</title>
  <style>
    *{box-sizing:border-box}body{margin:0;background:#f1f4f7;color:#263b51;
      font-family:"Microsoft YaHei UI",sans-serif}.top{height:58px;display:flex;align-items:center;
      justify-content:space-between;padding:0 28px;background:#fff;border-bottom:1px solid #dce2e8}
    main{max-width:1040px;margin:0 auto;padding:26px}h1{font:500 26px Georgia,"Songti SC",serif}
    .toolbar{display:grid;grid-template-columns:repeat(4,1fr) 110px;gap:8px;margin:16px 0}
    .result-search{max-width:1040px;margin:16px auto 0;padding:0}
    .result-search form{display:grid;grid-template-columns:1fr auto;gap:8px;padding:14px;border:1px solid #dce2e8;border-radius:14px;background:#fff}
    #subLibraries{display:flex;grid-column:1/-1;gap:8px;margin-top:4px}
    #subLibraries a{padding:6px 10px;border-radius:8px;color:#5e7183;text-decoration:none;font-size:10px}
    #subLibraries a.current{background:#eaf2fa;color:#2c6799;font-weight:700}
    #searchCondition{grid-column:1/-1;color:#788594;font-size:10px}
    select,button{height:37px;border:1px solid #d8e0e7;border-radius:8px;background:#fff;
      padding:0 9px;font:11px "Microsoft YaHei UI",sans-serif}button{background:#2d5e8c;color:#fff;border:0}
    .item{display:block;padding:15px 18px;margin-bottom:9px;border:1px solid #dde3e9;border-radius:12px;background:#fff}
    .t{display:flex;align-items:center;gap:12px}.row-sort,.row-check{display:flex;align-items:center;gap:5px;color:#7f8b98;font-size:9px}
    .row-sort b{min-width:24px;color:#4f6578}h4{margin:0}h4 a{color:#285f91;text-decoration:none}.info{display:flex;gap:13px;margin-top:7px;color:#7b8794;font-size:10px}
    .info a{color:#6f8396;text-decoration:none}.filtrater-box{margin:10px 0;color:#788594;font-size:11px}
    .pagination{display:flex;align-items:center;justify-content:center;gap:14px;margin-top:20px;font-size:11px}
    .pagination a{color:#346d9d;text-decoration:none}.empty{padding:45px;text-align:center;color:#7f8b97}
    .list-tool-wrap{display:flex;justify-content:flex-end;margin:12px 0}.down-all{padding:9px 14px;border-radius:8px;background:#2d5e8c;color:#fff;text-decoration:none}
    .articleSelect{position:relative;margin-right:10px;padding:8px 12px;border:1px solid #d8e0e7;border-radius:8px;background:#fff;font-size:10px}
    #recordPage{position:absolute;right:0;top:30px;z-index:10;padding:6px;border:1px solid #d8e0e7;border-radius:8px;background:#fff;box-shadow:0 10px 24px rgba(20,35,50,.12)}
    #recordPage dd{margin:0;padding:6px 10px;cursor:pointer}.group-switch{display:flex;gap:8px;margin:10px 0;color:#718091;font-size:10px}
    [name="bathDownloadContainer"]{position:fixed;inset:0;display:none;place-items:center;background:rgba(19,32,44,.58);z-index:30}
    .batch-dialog{width:370px;padding:24px;border-radius:16px;background:#fff;box-shadow:0 24px 70px rgba(0,0,0,.28)}
    .batch-dialog label{display:flex;align-items:center;gap:7px;margin-top:10px;color:#536678;font-size:11px}
    #batchDownload{width:100%;margin-top:16px;background:#2d5e8c;color:#fff}
    #captcha-overlay{position:fixed;inset:0;display:none;place-items:center;background:rgba(20,32,44,.58);z-index:20}
    #captcha-overlay div{padding:28px;border-radius:16px;background:#fff}
  </style>
</head>
<body>
  <header class="top"><strong>法规检索结果</strong><span>${escapeHtml(keyword)}</span></header>
  <section class="result-search">
    <form id="pkulaw-result-search-form" action="/pkulaw/search" method="get">
      <select name="SearchKeywordType" style="display:none">
        <option value="Title" ${searchType === "Title" ? "selected" : ""}>标题</option>
        <option value="FullText" ${searchType === "FullText" ? "selected" : ""}>全文</option>
      </select>
      <input type="hidden" name="Library" value="${escapeHtml(library)}">
      <input type="hidden" name="Pager.PageSize" value="${pageSize}">
      <input type="hidden" name="group" value="${groupMode}">
      <input id="txtSearch" name="keyword" value="${escapeHtml(keyword)}">
      <button id="pkulaw-search-submit" type="submit">精确检索</button>
      <div id="subLibraries">
        <a library="chl" class="${library === "chl" ? "current" : ""}" onclick="setResultLibrary('chl',this);return false">中央法规</a>
        <a library="lar" class="${library === "lar" ? "current" : ""}" onclick="setResultLibrary('lar',this);return false">地方法规</a>
      </div>
      <div id="searchCondition">检索条件：${escapeHtml(keyword)} · 检索字段：${escapeHtml(searchType)} · 子库：${library === "lar" ? "地方法规" : "中央法规"}</div>
    </form>
  </section>
  <main>
    <h1>“${escapeHtml(keyword)}”的检索结果</h1>
    <div class="toolbar">
      ${omitYear ? "" : `<select name="year">
        ${["全部年份","2025","2024","2023","2022","2021","2020","2019","2018"].map(value => `<option ${params.get("year") === value ? "selected" : ""}>${value}</option>`).join("")}
      </select>`}
      <select name="province">
        ${["全部省份","内蒙古自治区","北京市"].map(value => `<option ${params.get("province") === value ? "selected" : ""}>${value}</option>`).join("")}
      </select>
      <select name="timeliness">
        ${["全部（仅记录元数据，不筛选）","现行有效","已被修改","已废止"].map(value => `<option ${params.get("timeliness") === value ? "selected" : ""}>${value}</option>`).join("")}
      </select>
      <select name="pageSize">
        ${["10","20","50","100"].map(value => `<option ${params.get("pageSize") === value ? "selected" : ""}>${value}</option>`).join("")}
      </select>
      <button id="pkulaw-filter-submit" type="button">应用筛选</button>
    </div>
    <div class="filtrater-box">共找到 <span class="total"><strong>${matched.length}</strong></span> 条</div>
    <div class="list-tool-wrap">
      <div class="articleSelect" data-current-size="${pageSize}">
        <h4>${pageSize} 条/页</h4>
        <div id="recordPage" class="drop-wrap">
          <dl>${[10,20,50,100].map(value => `<dd class="${value === pageSize ? "currentSelect" : ""}">${value} 条/页</dd>`).join("")}</dl>
        </div>
      </div>
      <a class="down-all" href="#" onclick="openBatchDialog();return false">批量下载</a>
    </div>
    <form id="right_form" class="group-switch">
      <label><input name="group" type="radio" value="0" ${groupMode === "0" ? "checked" : ""}>按效力分组</label>
      <label><input name="group" type="radio" value="2" ${groupMode === "2" ? "checked" : ""}>不分组</label>
    </form>
    <section id="pkulaw-results" data-page="${safePage}">
      ${empty ? '<div class="empty">抱歉，未找到相关结果</div>' : rows}
    </section>
    <nav class="pagination" data-page="${safePage}" data-total="${totalPages}">
      <a href="?${prevParams.toString()}">上一页</a>
      <span>${pagination}</span>
      <a href="?${nextParams.toString()}">下一页</a>
    </nav>
    <div name="bathDownloadContainer">
      <div class="batch-dialog">
        <strong>批量下载设置</strong>
        <input type="hidden" id="catalog-format" value="xlsx">
        <label><input type="radio" name="batchDownload" value="catalogExcel" checked>目录 Excel</label>
        <label><input type="radio" name="batchDownload" value="fullWord">全文 Word ZIP</label>
        <button id="batchDownload" type="button">确认下载</button>
      </div>
    </div>
    <div id="captcha-overlay"><div><strong>请拖动滑块完成验证</strong><p class="drag_text">拖动滑块验证</p></div></div>
  </main>
  <script>
    function setResultLibrary(value, element) {
      document.querySelector('input[name="Library"]').value = value;
      document.querySelectorAll('#subLibraries a').forEach(item => item.classList.remove('current'));
      element.classList.add('current');
    }
    function applyResultQuery(updates) {
      const params = new URLSearchParams(location.search);
      for (const [name, value] of Object.entries(updates)) {
        if (value === null || value === undefined || value === '') params.delete(name);
        else params.set(name, value);
      }
      params.set('page', '1');
      location.href = '/pkulaw/search?' + params.toString();
    }
    document.querySelector('#pkulaw-filter-submit').addEventListener('click', () => {
      const params = new URLSearchParams(location.search);
      for (const name of ['year','province','timeliness','pageSize']) {
        params.set(name, document.querySelector('select[name="' + name + '"]').value);
      }
      const group = document.querySelector('input[name="group"]:checked')?.value;
      params.set('group', group || '0');
      params.set('page', '1');
      location.href = '/pkulaw/search?' + params.toString();
    });
    document.querySelectorAll('#recordPage dd').forEach(item => {
      item.addEventListener('click', () => {
        const size = (item.textContent || '').match(/(\\d+)/)?.[1] || '20';
        document.querySelector('select[name="pageSize"]').value = size;
        setTimeout(() => document.querySelector('#pkulaw-filter-submit').click(), 50);
      });
    });
    document.querySelectorAll('#right_form input[name="group"]').forEach(item => {
      item.addEventListener('change', () => {
        setTimeout(() => applyResultQuery({ group: item.value }), 50);
      });
    });
    ${keyword.includes("滑块测试") ? `{
      const overlay = document.querySelector('#captcha-overlay');
      if (!sessionStorage.getItem('codex-delayed-captcha-shown')) {
        sessionStorage.setItem('codex-delayed-captcha-shown', '1');
        overlay.style.display='grid';
        setTimeout(() => {
          overlay.style.display='none';
        }, 1500);
      }
    }` : ""}
    function openBatchDialog() {
      document.querySelector('[name="bathDownloadContainer"]').style.display = 'grid';
    }
    document.querySelector('#batchDownload').addEventListener('click', () => {
      const ids = [...document.querySelectorAll('input[name="recordList"]:checked')].map(item => item.value);
      if (!ids.length) {
        alert('请先选择要下载的政策');
        return;
      }
      if (ids.length > 50) {
        alert('单批不能超过50条');
        return;
      }
      const format = document.querySelector('input[name="batchDownload"]:checked').value;
      const catalogFormat = document.querySelector('#catalog-format').value;
      document.querySelector('[name="bathDownloadContainer"]').style.display = 'none';
      location.href = '/pkulaw/batch-file?' + new URLSearchParams({
        format,
        catalogFormat,
        ids: ids.join(','),
        keyword: ${JSON.stringify(keyword)}
      }).toString();
    });
  </script>
</body>
</html>`;
}

function detailPageHtml(policy: FixturePolicy): string {
  const wordExtension = policy.wordExtension || ".docx";
  return `<!doctype html>
<html lang="zh-CN" data-auth-ready="true" data-pkulaw-profile="fixture">
<head>
  <meta charset="utf-8"><title>${escapeHtml(policy.title)}</title>
  <style>
    body{margin:0;background:#f2f5f7;color:#263b50;font-family:"Microsoft YaHei UI",sans-serif}
    main{max-width:980px;margin:24px auto;padding:30px;background:#fff;border:1px solid #dce2e8;border-radius:16px}
    .title{margin:0 0 18px;font:500 29px Georgia,"Songti SC",serif}.fields{display:grid;gap:8px;padding:0;list-style:none}
    .fields li{display:grid;grid-template-columns:100px 1fr;padding:7px 0;border-bottom:1px solid #edf1f4;font-size:11px}
    .fields strong{color:#788592}.box{color:#41566a}.content{margin-top:24px;padding:22px;background:#fafbfc;
      border-radius:11px;font-size:13px;line-height:1.9}
    .download{margin-top:18px;padding:12px 16px;display:inline-block;border-radius:9px;background:#2d5e8c;color:#fff;cursor:pointer}
  </style>
</head>
<body>
  <main>
    <h1 class="fulltext-wrap"><div class="title">${escapeHtml(policy.title)}</div></h1>
    <ul class="fields">
      <li><strong>制定机关</strong><div class="box">${escapeHtml(policy.issuingAuthority)}</div></li>
      <li><strong>发文字号</strong><div class="box">${escapeHtml(policy.documentNumber)}</div></li>
      <li><strong>公布日期</strong><div class="box">${escapeHtml(policy.publicationDate)}</div></li>
      <li><strong>施行日期</strong><div class="box">${escapeHtml(policy.effectiveDate)}</div></li>
      <li><strong>时效性</strong><div class="box">${escapeHtml(policy.validity)}</div></li>
      <li><strong>效力位阶</strong><div class="box">${escapeHtml(policy.effectLevel)}</div></li>
    </ul>
    <div class="fulltext-wrap"><div class="content">${escapeHtml(policy.body)}</div></div>
    <li class="singleDownload" filetype="fullWord">
      <a class="download" href="/pkulaw/download/${policy.id}${wordExtension}" onclick="event.stopPropagation()">下载WORD</a>
    </li>
  </main>
  <script>
    document.querySelector('li.singleDownload[filetype="fullWord"]').addEventListener('click', event => {
      if (!event.target.closest('a')) location.href = '/pkulaw/download/${policy.id}${wordExtension}';
    });
  </script>
</body>
</html>`;
}

function makeDocx(policy: FixturePolicy): Uint8Array {
  return makeDocxBytes(policy.title, policy.body);
}

function makeLegacyDoc(): Uint8Array {
  const data = Buffer.alloc(512);
  Buffer.from([0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1]).copy(data);
  data.write("PKULAW_LEGACY_DOC_FIXTURE", 8, "ascii");
  return new Uint8Array(data);
}

function makeDocxBytes(title: string, body: string): Uint8Array {
  const documentXml = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>${escapeHtml(title)}</w:t></w:r></w:p>
    <w:p><w:r><w:t>${escapeHtml(body)}</w:t></w:r></w:p>
  </w:body>
</w:document>`;
  return zipSync({
    "[Content_Types].xml": strToU8(`<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>`),
    "_rels/.rels": strToU8(`<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>`),
    "word/document.xml": strToU8(documentXml)
  });
}

function governmentSearchHtml(title: string): string {
  const matches = GOVERNMENT_POLICIES.filter((policy) => policy.title === title);
  const rows = matches
    .map(
      (policy) => `<article class="gov-result">
        <a href="/government/detail/${policy.id}.html"
           data-official="${policy.official ? "true" : "false"}">
          ${escapeHtml(policy.title)}
        </a>
        <p>${escapeHtml(policy.authority)} · ${escapeHtml(policy.publicationDate)}</p>
      </article>`
    )
    .join("");
  return `<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>政府官网精确检索</title>
<style>body{margin:0;padding:36px;background:#f4f6f8;font-family:"Microsoft YaHei UI",sans-serif;color:#253b50}
main{max-width:850px;margin:auto}.gov-result{display:block;padding:16px;margin:10px 0;border-radius:12px;
background:#fff;border:1px solid #dce2e8}.gov-result a{color:#245f91;text-decoration:none;font-weight:700}
.gov-result p{color:#7d8996;font-size:11px}</style></head>
<body><main><h1>政府官网检索：${escapeHtml(title)}</h1>
${rows || '<p class="empty">未找到标题完全一致的官方结果</p>'}</main></body></html>`;
}

function governmentDetailHtml(policy: GovernmentFixture): string {
  const attachment = policy.attachment
    ? `<p><a href="/government/files/${policy.id}.docx">政策原文 Word 附件</a></p>`
    : `<p>本页面没有原生 Word 附件，以下为官方 HTML 正文。</p>`;
  return `<!doctype html>
<html lang="zh-CN" data-auth-ready="true">
<head><meta charset="utf-8"><title>${escapeHtml(policy.title)}</title>
<style>body{margin:0;background:#f3f5f7;font-family:"Microsoft YaHei UI",sans-serif;color:#253b50}
main{max-width:900px;margin:28px auto;padding:30px;border-radius:16px;background:#fff;border:1px solid #dce2e8}
#gov-title{font:500 30px Georgia,"Songti SC",serif}.meta{display:grid;grid-template-columns:100px 1fr;
gap:7px;margin:18px 0;padding:15px;border-radius:10px;background:#f7f9fa;font-size:11px}
#gov-body{margin-top:18px;padding:20px;border-radius:10px;background:#fbfcfd;line-height:1.9;font-size:13px}
a{color:#286798}</style></head>
<body><main data-official="${policy.official ? "true" : "false"}">
  <h1 id="gov-title">${escapeHtml(policy.title)}</h1>
  <div class="meta">
    <span>制定机关</span><b data-gov-field="issuingAuthority">${escapeHtml(policy.authority)}</b>
    <span>发文字号</span><b data-gov-field="documentNumber">${escapeHtml(policy.documentNumber)}</b>
    <span>公布日期</span><b data-gov-field="publicationDate">${escapeHtml(policy.publicationDate)}</b>
    <span>施行日期</span><b data-gov-field="effectiveDate">${escapeHtml(policy.effectiveDate)}</b>
    <span>时效性</span><b data-gov-field="validity">${escapeHtml(policy.validity)}</b>
    <span>效力位阶</span><b data-gov-field="effectLevel">${escapeHtml(policy.effectLevel)}</b>
  </div>
  ${attachment}
  <div id="gov-body">${escapeHtml(policy.body)}</div>
</main></body></html>`;
}

function makeCatalogXlsx(policies: FixturePolicy[]): Uint8Array {
  const rows = [
    ["序号", "标题", "原文链接"],
    ...policies.map((policy, index) => [
      String(index + 1),
      policy.title,
      `${"http://127.0.0.1"}/pkulaw/detail/${policy.id}.html`
    ])
  ];
  const cell = (reference: string, value: string): string =>
    `<c r="${reference}" t="inlineStr"><is><t>${escapeHtml(value)}</t></is></c>`;
  const sheetRows = rows
    .map((row, rowIndex) => {
      const cells = row
        .map((value, columnIndex) => {
          const column = String.fromCharCode(65 + columnIndex);
          return cell(`${column}${rowIndex + 1}`, value);
        })
        .join("");
      return `<row r="${rowIndex + 1}">${cells}</row>`;
    })
    .join("");
  return zipSync({
    "[Content_Types].xml": strToU8(`<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>`),
    "_rels/.rels": strToU8(`<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>`),
    "xl/workbook.xml": strToU8(`<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="政策目录" sheetId="1" r:id="rId1"/></sheets>
</workbook>`),
    "xl/_rels/workbook.xml.rels": strToU8(`<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>`),
    "xl/worksheets/sheet1.xml": strToU8(`<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>${sheetRows}</sheetData>
</worksheet>`)
  });
}

const NATIVE_CATALOG_XLS_SCRIPT = String.raw`
import io
import json
import sys
import xlwt

rows = json.loads(sys.stdin.buffer.read().decode("utf-8"))
book = xlwt.Workbook(encoding="utf-8")
sheet = book.add_sheet("政策目录")
for column, header in enumerate(("序号", "标题", "原文链接")):
    sheet.write(0, column, header)
for row_index, policy in enumerate(rows, start=1):
    sheet.write(row_index, 0, str(row_index))
    sheet.write(row_index, 1, str(policy["title"]))
    sheet.write(row_index, 2, str(policy["url"]))
output = io.BytesIO()
book.save(output)
sys.stdout.buffer.write(output.getvalue())
`;

async function makeCatalogXls(
  policies: FixturePolicy[]
): Promise<Uint8Array> {
  const pythonPath = process.env.PKULAW_PYTHON?.trim() || "python";
  const rows = policies.map((policy) => ({
    title: policy.title,
    url: `/pkulaw/detail/${policy.id}.html`
  }));
  return await new Promise<Uint8Array>((resolve, reject) => {
    const child = spawn(
      pythonPath,
      ["-c", NATIVE_CATALOG_XLS_SCRIPT],
      {
        stdio: ["pipe", "pipe", "pipe"],
        windowsHide: true
      }
    );
    const chunks: Buffer[] = [];
    const errors: Buffer[] = [];
    const timer = setTimeout(() => {
      child.kill();
      reject(new Error("Timed out while generating native BIFF XLS"));
    }, 15_000);
    child.stdout.on("data", (chunk: Buffer) => {
      chunks.push(chunk);
      if (chunks.reduce((total, item) => total + item.length, 0) > 32 * 1024 * 1024) {
        child.kill();
        clearTimeout(timer);
        reject(new Error("Native BIFF XLS fixture exceeded 32 MiB"));
      }
    });
    child.stderr.on("data", (chunk: Buffer) => errors.push(chunk));
    child.once("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });
    child.once("close", (code) => {
      clearTimeout(timer);
      if (code !== 0) {
        reject(
          new Error(
            Buffer.concat(errors).toString("utf8").trim() ||
              `xlwt exited with code ${code}`
          )
        );
        return;
      }
      const data = new Uint8Array(Buffer.concat(chunks));
      const signature = Buffer.from(data.subarray(0, 8)).toString("hex");
      if (signature !== "d0cf11e0a1b11ae1") {
        reject(new Error(`Fixture catalog is not BIFF XLS: ${signature}`));
        return;
      }
      resolve(data);
    });
    child.stdin.end(JSON.stringify(rows));
  });
}

function makeCatalogHtmlXls(policies: FixturePolicy[]): Uint8Array {
  const rows = policies
    .map(
      (policy, index) => `<tr><td>${index + 1}</td><td>${escapeHtml(policy.title)}</td>
        <td><a href="/pkulaw/detail/${policy.id}.html">${policy.id}</a></td></tr>`
    )
    .join("");
  return strToU8(`<html><head><meta charset="utf-8"></head><body>
    <table><tr><th>序号</th><th>标题</th><th>原文链接</th></tr>${rows}</table>
  </body></html>`);
}

function makeWordZip(policies: FixturePolicy[]): Uint8Array {
  const entries: Record<string, Uint8Array> = {};
  for (const policy of policies) {
    entries[`${policy.id}_${policy.title}.docx`] = makeDocx(policy);
  }
  return zipSync(entries);
}

function selectorDiagnosticPageHtml(mode: string): string {
  const input =
    mode === "missing-input"
      ? ""
      : mode === "fallback"
        ? '<input type="text" class="search-input" placeholder="请输入检索关键词">'
        : '<input type="text" id="txtSearch" placeholder="稳定检索框">' +
          (mode === "multiple"
            ? '<input type="text" class="decoy-input" placeholder="干扰输入框">'
            : "");
  const submit =
    mode === "missing-submit"
      ? ""
      : mode === "fallback"
        ? '<button type="button" class="fallback-submit">检索</button>'
        : '<button type="submit" id="pkulaw-search-submit">精确检索</button>' +
          (mode === "multiple"
            ? '<button type="button" class="decoy-submit">干扰检索</button>'
            : "");
  return `<!doctype html>
<html lang="zh-CN" data-pkulaw-profile="fixture">
<head><meta charset="utf-8"><title>选择器诊断夹具</title></head>
<body>
  <div id="subLibraries"><a library="chl">中央法规</a><a library="lar">地方法规</a></div>
  <form>${input}${submit}</form>
</body>
</html>`;
}

export class FixtureServer {
  private server: http.Server | null = null;
  private baseUrl = "";
  private readonly detailAttempts = new Map<string, number>();
  private readonly failedBatchZips = new Set<string>();

  async start(): Promise<string> {
    if (this.server && this.baseUrl) {
      return this.baseUrl;
    }
    this.server = http.createServer((request, response) => {
      const url = new URL(request.url || "/", "http://127.0.0.1");
      const authenticated = (request.headers.cookie || "").includes(
        "pkulaw_fixture_session=authenticated"
      );

      if (url.pathname === "/redirect-cross-host") {
        const port = new URL(this.baseUrl).port;
        response.writeHead(302, {
          Location: `http://localhost:${port}/app`
        });
        response.end();
        return;
      }
      if (url.pathname === "/slow-app") {
        setTimeout(() => {
          response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
          response.end(LOGIN_HTML);
        }, 1_200);
        return;
      }
      if (url.pathname.startsWith("/pkulaw-v6-fixtures/")) {
        const fileName = url.pathname.slice("/pkulaw-v6-fixtures/".length);
        if (!V6_FIXTURE_FILES.has(fileName)) {
          response.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
          response.end("V6 fixture not found");
          return;
        }
        const fixturePath = path.resolve(
          process.cwd(),
          "../../engine/tests/fixtures/pkulaw-v6",
          fileName
        );
        response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
        response.end(fs.readFileSync(fixturePath));
        return;
      }
      if (url.pathname === "/selector-fixture") {
        const mode = url.searchParams.get("mode") || "stable";
        response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
        response.end(selectorDiagnosticPageHtml(mode));
        return;
      }
      if (url.pathname === "/pkulaw" || url.pathname === "/pkulaw/") {
        if (!authenticated) {
          response.writeHead(302, { Location: "/" });
          response.end();
          return;
        }
        response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
        response.end(searchPageHtml(url.searchParams.get("noYear") === "1"));
        return;
      }
      if (url.pathname === "/pkulaw/search") {
        if (!authenticated) {
          response.writeHead(302, { Location: "/" });
          response.end();
          return;
        }
        const keyword = url.searchParams.get("keyword") || "";
        if (keyword.includes("额度测试")) {
          response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
          response.end(`<!doctype html><html><body>
            <h1>今日下载次数已达上限</h1>
            <p>学校的今日正文查看数和下载次数已经用满，请明日再试。</p>
          </body></html>`);
          return;
        }
        response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
        response.end(resultsPageHtml(url.searchParams, this.baseUrl));
        return;
      }
      const detailMatch = url.pathname.match(/^\/pkulaw\/detail\/([^/]+)\.html$/);
      if (detailMatch) {
        if (!authenticated) {
          response.writeHead(302, { Location: "/" });
          response.end();
          return;
        }
        const policy = POLICIES.find((item) => item.id === detailMatch[1]);
        if (!policy) {
          response.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
          response.end("policy not found");
          return;
        }
        let responsePolicy = policy;
        if (policy.id === "retry-stage3-001") {
          const attempts = (this.detailAttempts.get(policy.id) || 0) + 1;
          this.detailAttempts.set(policy.id, attempts);
          if (attempts === 1) {
            responsePolicy = {
              ...policy,
              title: "错误详情页",
              body: "首次详情页故意返回错误标题。"
            };
          }
        }
        response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
        response.end(detailPageHtml(responsePolicy));
        return;
      }
      const downloadMatch = url.pathname.match(
        /^\/pkulaw\/download\/([^/]+)\.(doc|docx)$/
      );
      if (downloadMatch) {
        if (!authenticated) {
          response.writeHead(302, { Location: "/" });
          response.end();
          return;
        }
        const policy = POLICIES.find((item) => item.id === downloadMatch[1]);
        if (!policy) {
          response.writeHead(404);
          response.end("policy not found");
          return;
        }
        if (policy.id === "broken-word-policy") {
          const invalid = strToU8("THIS_IS_NOT_A_VALID_DOCX");
          response.writeHead(200, {
            "Content-Type":
              "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "Content-Disposition": 'attachment; filename="broken-word-policy.docx"',
            "Content-Length": String(invalid.byteLength)
          });
          response.end(Buffer.from(invalid));
          return;
        }
        const extension = `.${downloadMatch[2].toLowerCase()}`;
        const data = extension === ".doc" ? makeLegacyDoc() : makeDocx(policy);
        const sendDownload = (): void => {
          response.writeHead(200, {
            "Content-Type": extension === ".doc"
              ? "application/msword"
              : "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "Content-Disposition": `attachment; filename="${policy.id}${extension}"`,
            "Content-Length": String(data.byteLength)
          });
          response.end(Buffer.from(data));
        };
        if (policy.id === "pause-boundary-policy") {
          setTimeout(sendDownload, 1_200);
        } else {
          sendDownload();
        }
        return;
      }
      if (url.pathname === "/pkulaw/batch-file") {
        if (!authenticated) {
          response.writeHead(302, { Location: "/" });
          response.end();
          return;
        }
        const ids = (url.searchParams.get("ids") || "").split(",").filter(Boolean);
        const format = url.searchParams.get("format") || "";
        const catalogFormat = url.searchParams.get("catalogFormat") || "xlsx";
        const selected = ids
          .map((id) => POLICIES.find((policy) => policy.id === id))
          .filter((policy): policy is FixturePolicy => Boolean(policy));
        if (!selected.length || selected.length > 50) {
          response.writeHead(400, { "Content-Type": "text/plain; charset=utf-8" });
          response.end("invalid batch size");
          return;
        }
        const safeName = `batch-${selected[0].id}-${selected.length}`;
        const pauseBoundaryDelayMs = selected.some(
          (policy) => policy.id === "pause-boundary-policy"
        )
          ? 1_200
          : 0;
        const writeBufferedDownload = (
          contentType: string,
          filename: string,
          data: Uint8Array
        ): void => {
          const send = (): void => {
            response.writeHead(200, {
              "Content-Type": contentType,
              "Content-Disposition": `attachment; filename="${filename}"`,
              "Content-Length": String(data.byteLength)
            });
            response.end(Buffer.from(data));
          };
          if (pauseBoundaryDelayMs) {
            setTimeout(send, pauseBoundaryDelayMs);
          } else {
            send();
          }
        };
        if (format === "catalogExcel") {
          const extension =
            catalogFormat === "xls" || catalogFormat === "html-xls"
              ? "xls"
              : "xlsx";
          const contentType =
            extension === "xlsx"
              ? "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
              : "application/vnd.ms-excel";
          if (catalogFormat === "xls") {
            void makeCatalogXls(selected)
              .then((data) => {
                writeBufferedDownload(
                  contentType,
                  `${safeName}.${extension}`,
                  data
                );
              })
              .catch((error) => {
                response.writeHead(500, {
                  "Content-Type": "text/plain; charset=utf-8"
                });
                response.end(`native XLS fixture generation failed: ${String(error)}`);
              });
            return;
          }
          const data =
            catalogFormat === "html-xls"
              ? makeCatalogHtmlXls(selected)
              : makeCatalogXlsx(selected);
          writeBufferedDownload(contentType, `${safeName}.${extension}`, data);
          return;
        }
        if (format === "fullWord") {
          const failureKey = `${ids[0]}:${ids.length}`;
          if (
            selected.some((policy) => policy.id === "batch-policy-51") &&
            !this.failedBatchZips.has(failureKey)
          ) {
            this.failedBatchZips.add(failureKey);
            const invalid = strToU8("INVALID_ZIP_ONCE");
            response.writeHead(200, {
              "Content-Type": "application/zip",
              "Content-Disposition": `attachment; filename="${safeName}.zip"`,
              "Content-Length": String(invalid.byteLength)
            });
            response.end(Buffer.from(invalid));
            return;
          }
          const data = makeWordZip(selected);
          writeBufferedDownload("application/zip", `${safeName}.zip`, data);
          return;
        }
        response.writeHead(400, { "Content-Type": "text/plain; charset=utf-8" });
        response.end("unsupported batch format");
        return;
      }
      if (url.pathname === "/government/search") {
        const title = url.searchParams.get("title") || "";
        response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
        response.end(governmentSearchHtml(title));
        return;
      }
      const governmentDetailMatch = url.pathname.match(
        /^\/government\/detail\/([^/]+)\.html$/
      );
      if (governmentDetailMatch) {
        const policy = GOVERNMENT_POLICIES.find(
          (item) => item.id === governmentDetailMatch[1]
        );
        if (!policy) {
          response.writeHead(404);
          response.end("government fixture not found");
          return;
        }
        response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
        response.end(governmentDetailHtml(policy));
        return;
      }
      if (url.pathname === "/government/files/gov-native.docx") {
        const policy = GOVERNMENT_POLICIES.find((item) => item.id === "gov-native");
        if (!policy) {
          response.writeHead(404);
          response.end("government attachment not found");
          return;
        }
        const data = makeDocxBytes(policy.title, policy.body);
        response.writeHead(200, {
          "Content-Type":
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
          "Content-Disposition": 'attachment; filename="gov-native.docx"',
          "Content-Length": String(data.byteLength)
        });
        response.end(Buffer.from(data));
        return;
      }
      if (url.pathname === "/download") {
        response.writeHead(200, {
          "Content-Type": "text/plain; charset=utf-8",
          "Content-Disposition": "attachment; filename=stage2-download.txt"
        });
        response.end("STAGE2_ELECTRON_DOWNLOAD_OK\n");
        return;
      }
      if (url.pathname === "/child") {
        response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
        response.end(CHILD_HTML);
        return;
      }
      if (url.pathname === "/fixture-login") {
        response.writeHead(302, {
          Location: "/app",
          "Set-Cookie":
            "pkulaw_fixture_session=authenticated; Path=/; HttpOnly; SameSite=Lax; Max-Age=86400"
        });
        response.end();
        return;
      }
      if (url.pathname === "/app" && authenticated) {
        response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
        response.end(APP_HTML);
        return;
      }
      if (url.pathname === "/" && authenticated) {
        response.writeHead(302, { Location: "/app" });
        response.end();
        return;
      }
      response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      response.end(LOGIN_HTML);
    });

    await new Promise<void>((resolve, reject) => {
      this.server?.once("error", reject);
      this.server?.listen(0, "127.0.0.1", () => resolve());
    });
    const address = this.server.address() as AddressInfo;
    this.baseUrl = `http://127.0.0.1:${address.port}/`;
    return this.baseUrl;
  }

  async stop(): Promise<void> {
    const server = this.server;
    this.server = null;
    this.baseUrl = "";
    if (!server) {
      return;
    }
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
}
