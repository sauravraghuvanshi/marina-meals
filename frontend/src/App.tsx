import { useEffect, useMemo, useRef, useState } from "react";
import { VoiceLiveClient, type VoiceState, type ToolEvent, type ShownPanel } from "./voice/voiceLiveClient";
import { STRINGS, type Lang, categoryLabel } from "./i18n";

type MenuItem = {
  id: string;
  name: string;
  tamil_name?: string;
  price: number;
  veg: boolean;
  available: boolean;
  daily_special: boolean;
  description?: string;
  image?: string;
  category: string;
};

type Store = {
  id: string;
  name: string;
  area: string;
  tamil_area?: string;
  address: string;
  phone: string;
  hours: { open: string; close: string };
  landmark?: string;
  open_now: boolean;
};

type CartLine = { id: string; name: string; qty: number; unit_price: number; line_total: number };

const FALLBACK_IMG = "https://images.unsplash.com/photo-1505253758473-96b7015fcd40?w=600&h=400&fit=crop";

export default function App() {
  const [lang, setLang] = useState<Lang>("en");
  const t = STRINGS[lang];

  const [menu, setMenu] = useState<MenuItem[]>([]);
  const [stores, setStores] = useState<Store[]>([]);
  const [state, setState] = useState<VoiceState>("idle");
  const [error, setError] = useState<string>("");
  const [vegOnly, setVegOnly] = useState(false);
  const [activeCat, setActiveCat] = useState<string>("all");

  const [highlightItem, setHighlightItem] = useState<string | null>(null);
  const [highlightStore, setHighlightStore] = useState<string | null>(null);
  const [cart, setCart] = useState<CartLine[]>([]);
  const [confirmation, setConfirmation] = useState<string | null>(null);
  const [orderTotal, setOrderTotal] = useState<number | null>(null);
  const [pickupStore, setPickupStore] = useState<Store | null>(null);
  const [agentCaption, setAgentCaption] = useState("");
  const [userCaption, setUserCaption] = useState("");
  const [shownPanel, setShownPanel] = useState<ShownPanel | null>(null);
  const clientRef = useRef<VoiceLiveClient | null>(null);
  const captionTimer = useRef<number | null>(null);

  useEffect(() => {
    fetch("/api/menu").then((r) => r.json()).then((d) => setMenu(d.items));
    fetch("/api/store").then((r) => r.json()).then((d) => setStores(d.stores));
  }, []);

  const categories = useMemo(() => {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const i of menu) if (!seen.has(i.category)) { seen.add(i.category); out.push(i.category); }
    return out;
  }, [menu]);

  const filtered = useMemo(() => {
    return menu.filter((i) =>
      (!vegOnly || i.veg) &&
      (activeCat === "all" || i.category === activeCat)
    );
  }, [menu, vegOnly, activeCat]);

  const grouped = useMemo(() => {
    const by: Record<string, MenuItem[]> = {};
    for (const i of filtered) (by[i.category] ||= []).push(i);
    return by;
  }, [filtered]);

  function handleTool(e: ToolEvent) {
    const r: any = e.result;
    if (e.name !== "show_on_screen") {
      setShownPanel(null);
    }
    if (e.name === "get_item_price" && r?.found) {
      flashHighlight(r.item.id);
      scrollToItem(r.item.id);
    } else if (e.name === "find_store" && r?.found && r?.store) {
      setHighlightStore(r.store.id);
    } else if (e.name === "place_order" && r?.ok) {
      setCart(r.items);
      setOrderTotal(r.total);
      setConfirmation(r.confirmation_number);
      setPickupStore(r.store);
    } else if (e.name === "get_todays_specials" && Array.isArray(r?.items)) {
      r.items.forEach((it: any, idx: number) => setTimeout(() => flashHighlight(it.id), idx * 400));
    }
  }

  function flashHighlight(id: string) {
    setHighlightItem(id);
    window.setTimeout(() => setHighlightItem((cur) => (cur === id ? null : cur)), 2800);
  }

  function scrollToItem(id: string) {
    const el = document.getElementById(`item-${id}`);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  async function toggle() {
    setError("");
    if (state === "idle" || state === "error") {
      const client = new VoiceLiveClient({
        onState: setState,
        onError: setError,
        onToolCall: handleTool,
        onShowOnScreen: (p) => setShownPanel(p),
        onUserSpeechStart: () => setShownPanel(null),
        onTranscriptDelta: (text, role) => {
          if (role === "agent") setAgentCaption((c) => (c + text).slice(-220));
          else setUserCaption((c) => (c + text).slice(-220));
          if (captionTimer.current) window.clearTimeout(captionTimer.current);
          captionTimer.current = window.setTimeout(() => { setAgentCaption(""); setUserCaption(""); }, 8000);
        },
      });
      clientRef.current = client;
      await client.start();
    } else {
      await clientRef.current?.stop();
      clientRef.current = null;
    }
  }

  async function reset() {
    await clientRef.current?.stop();
    clientRef.current = null;
    setCart([]); setConfirmation(null); setOrderTotal(null); setPickupStore(null);
    setHighlightItem(null); setHighlightStore(null);
    setShownPanel(null);
    setAgentCaption(""); setUserCaption(""); setError("");
  }

  const stateLabel: Record<VoiceState, string> = {
    idle: t.cta_talk, connecting: t.cta_connecting, listening: t.cta_listening,
    thinking: t.cta_thinking, speaking: t.cta_speaking, error: t.cta_error,
  };

  return (
    <div className="app">
      {/* ─── top nav ─── */}
      <nav className="nav">
        <div className="brand">
          <span className="brand-mark">◬</span>
          <span className="brand-name">Marina Meals</span>
          <span className="brand-sub" lang="ta">மரினா மீல்ஸ்</span>
        </div>
        <div className="nav-actions">
          <div className="lang-toggle" role="group" aria-label="language">
            <button
              className={lang === "en" ? "on" : ""}
              onClick={() => setLang("en")}
              aria-pressed={lang === "en"}
            >EN</button>
            <button
              className={lang === "ta" ? "on" : ""}
              onClick={() => setLang("ta")}
              aria-pressed={lang === "ta"}
              lang="ta"
            >தமிழ்</button>
          </div>
          <button className="ghost" onClick={reset}>↺ {t.reset}</button>
        </div>
      </nav>

      {/* ─── hero ─── */}
      <header className="hero">
        <div className="hero-left">
          <div className="eyebrow">CHENNAI · {new Date().toLocaleDateString(lang === "ta" ? "ta-IN" : "en-IN", { weekday: "long" }).toUpperCase()}</div>
          <h1 className="hero-title">
            {lang === "en" ? (
              <>A taste of <span className="accent">Chennai</span>, on call.</>
            ) : (
              <span lang="ta">சென்னையின் <span className="accent">சுவை</span>, ஒரு அழைப்பில்.</span>
            )}
          </h1>
          <p className="hero-sub">{t.tagline}</p>
          <p className="hero-sub-2">{t.sub_tagline}</p>

          <div className="cta-row">
            <button
              className={`mic mic-${state}`}
              onClick={toggle}
              disabled={state === "connecting"}
            >
              <span className="mic-icon">{state === "speaking" ? "🔊" : state === "listening" ? "🎙" : state === "thinking" ? "✦" : "🎤"}</span>
              {stateLabel[state]}
            </button>
            <div className="examples-pill">
              <div className="ex-title">{t.examples_title}</div>
              <div className="ex-body">{t.examples}</div>
            </div>
          </div>
          {error && <div className="err-row">⚠ {error}</div>}
        </div>
        <div className="hero-right">
          <div className="hero-card hero-card-1" style={{ backgroundImage: `url(${menu.find(m => m.id === "masala_dosa")?.image || ""})` }} />
          <div className="hero-card hero-card-2" style={{ backgroundImage: `url(${menu.find(m => m.id === "filter_coffee")?.image || ""})` }} />
          <div className="hero-card hero-card-3" style={{ backgroundImage: `url(${menu.find(m => m.id === "south_indian_meals")?.image || ""})` }} />
          <div className="hero-card hero-card-4" style={{ backgroundImage: `url(${menu.find(m => m.id === "chicken_biryani")?.image || ""})` }} />
        </div>
      </header>

      {/* ─── captions ─── */}
      {(agentCaption || userCaption) && (
        <section className="captions" aria-live="polite">
          {userCaption && <div className="cap cap-user"><span className="cap-tag">{t.you}</span>{userCaption}</div>}
          {agentCaption && <div className="cap cap-agent"><span className="cap-tag">{t.host}</span>{agentCaption}</div>}
        </section>
      )}

      {/* ─── filters ─── */}
      <section className="filters">
        <div className="chips">
          <button
            className={`chip ${activeCat === "all" ? "active" : ""}`}
            onClick={() => setActiveCat("all")}
          >{t.all}</button>
          {categories.map((c) => (
            <button
              key={c}
              className={`chip ${activeCat === c ? "active" : ""}`}
              onClick={() => setActiveCat(c)}
            >{categoryLabel(lang, c)}</button>
          ))}
        </div>
        <label className="veg-switch">
          <input type="checkbox" checked={vegOnly} onChange={(e) => setVegOnly(e.target.checked)} />
          <span className="track"><span className="thumb" /></span>
          <span className="veg-label">{t.veg_only}</span>
        </label>
      </section>

      {/* ─── main grid ─── */}
      <main className="grid">
        <section className="menu-col">
          {Object.entries(grouped).map(([cat, items]) => (
            <div key={cat} className="category">
              <h2 className="cat-title">
                <span className="cat-label">{categoryLabel(lang, cat)}</span>
                <span className="cat-count">{items.length}</span>
              </h2>
              <div className="items">
                {items.map((i) => (
                  <article
                    key={i.id}
                    id={`item-${i.id}`}
                    className={[
                      "item",
                      !i.available && "unavailable",
                      i.daily_special && "special",
                      highlightItem === i.id && "highlight",
                    ].filter(Boolean).join(" ")}
                  >
                    <div className="item-img" role="img" aria-label={i.name}>
                      <img
                        src={i.image || FALLBACK_IMG}
                        alt={i.name}
                        loading="lazy"
                        onError={(e) => { (e.currentTarget as HTMLImageElement).src = FALLBACK_IMG; }}
                      />
                      {i.daily_special && <span className="ribbon">★ {t.todays_special}</span>}
                      {!i.available && <span className="ribbon ribbon-muted">{t.unavailable}</span>}
                    </div>
                    <div className="item-body">
                      <div className="item-row">
                        <span className={`veg-dot ${i.veg ? "veg" : "nonveg"}`} title={i.veg ? "Veg" : "Non-veg"} />
                        <h3 className="item-name">{lang === "ta" && i.tamil_name ? i.tamil_name : i.name}</h3>
                      </div>
                      {lang === "en" && i.tamil_name && <div className="tamil-name" lang="ta">{i.tamil_name}</div>}
                      {lang === "ta" && <div className="english-name">{i.name}</div>}
                      {i.description && <p className="desc">{i.description}</p>}
                      <div className="price-row">
                        <span className="price">₹{i.price}</span>
                      </div>
                    </div>
                  </article>
                ))}
              </div>
            </div>
          ))}
        </section>

        {/* ─── sticky side ─── */}
        <aside className="side">
          <section className="panel cart">
            <div className="panel-head">
              <h2>{t.order}</h2>
              {confirmation && <span className="pill pill-ok">{confirmation}</span>}
            </div>
            {cart.length === 0 ? (
              <p className="empty">{t.empty_cart}</p>
            ) : (
              <>
                <ul className="cart-list">
                  {cart.map((l) => (
                    <li key={l.id}>
                      <span className="cart-qty">{l.qty}×</span>
                      <span className="cart-name">{l.name}</span>
                      <span className="cart-amt">₹{l.line_total}</span>
                    </li>
                  ))}
                </ul>
                {orderTotal !== null && (
                  <div className="total-row">
                    <span>{t.total}</span>
                    <strong>₹{orderTotal}</strong>
                  </div>
                )}
                {pickupStore && (
                  <div className="pickup-row">
                    <span className="pickup-label">{t.pickup}</span>
                    <strong>{pickupStore.name}</strong>
                  </div>
                )}
              </>
            )}
          </section>

          <section className="panel stores">
            <div className="panel-head"><h2>{t.branches}</h2></div>
            <ul className="store-list">
              {stores.map((s) => (
                <li key={s.id} className={highlightStore === s.id ? "highlight" : ""}>
                  <div className="store-top">
                    <strong>{lang === "ta" && s.tamil_area ? s.tamil_area : s.area}</strong>
                    <span className={`status ${s.open_now ? "open" : "closed"}`}>
                      <span className="dot" />{s.open_now ? t.open_now : t.closed}
                    </span>
                  </div>
                  {lang === "en" && s.tamil_area && <div className="store-sub" lang="ta">{s.tamil_area}</div>}
                  <div className="store-addr">{s.address}</div>
                  <div className="store-meta">
                    <span>📞 {s.phone}</span>
                    <span>🕒 {s.hours.open}–{s.hours.close}</span>
                  </div>
                </li>
              ))}
            </ul>
          </section>
        </aside>
      </main>

      {shownPanel && (() => {
        const items = shownPanel.item_ids
          .map((id) => menu.find((m) => m.id === id))
          .filter((m): m is MenuItem => !!m);
        return (
          <div className="shown-overlay" role="dialog" aria-modal="true" onClick={() => setShownPanel(null)}>
            <div className="shown-panel" onClick={(e) => e.stopPropagation()}>
              <div className="shown-head">
                <h2>{shownPanel.title}</h2>
                <button className="shown-close" onClick={() => setShownPanel(null)} aria-label="Close">×</button>
              </div>
              {items.length === 0 ? (
                <div className="shown-empty">No items to show.</div>
              ) : (
                <ol className="shown-list">
                  {items.map((it) => (
                    <li key={it.id} className="shown-item">
                      <img className="shown-img" src={it.image || FALLBACK_IMG} alt="" onError={(e) => ((e.currentTarget as HTMLImageElement).src = FALLBACK_IMG)} />
                      <div className="shown-text">
                        <div className="shown-name">
                          {lang === "ta" && it.tamil_name ? it.tamil_name : it.name}
                          <span className={`veg-dot ${it.veg ? "veg" : "non"}`} aria-hidden />
                        </div>
                        {lang === "en" && it.tamil_name && <div className="shown-sub" lang="ta">{it.tamil_name}</div>}
                      </div>
                      <div className="shown-price">₹{it.price}</div>
                    </li>
                  ))}
                </ol>
              )}
            </div>
          </div>
        );
      })()}

      <footer>{t.powered}</footer>
    </div>
  );
}
