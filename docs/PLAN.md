# Plan de implementare

## Structură

```text
news-aggregator/
  backend/
    app/
      api/            # rute FastAPI (health, stories, categories, sources, scan)
      connectors/     # interfața fetch_since + Demo, RSS, JSON API, Reuters Connect, FT, Scrapling
      services/       # normalizare, politici, clasificare, deduplicare, ingestie, căutare, retenție
      models/         # modele SQLAlchemy
      repositories/   # interogări pentru stories / sources / runs
      scheduler/      # APScheduler + coordonatorul de scanare (fără rulări paralele)
      config/         # settings (.env), sources.yaml, categories.yaml, entities/*.yaml
      demo/           # fixture-uri locale pentru DemoConnector
    alembic/          # migrații
    tests/
    pyproject.toml
  frontend/           # React + TypeScript + Vite (+ Radix Collapsible)
  scripts/            # dev, build, backup, instalare LaunchAgent
  launchd/            # șablon plist LaunchAgent
  data/               # news.db (SQLite WAL), ignorat în git
  logs/
```

## Etape

1. **Fundația:** settings, schema și migrația inițială (cu FTS5), `/health`, seed de categorii, fixture-uri demo, pagina principală și sidebar.
2. **Ingestie și unicitate:** DemoConnector, runner, APScheduler la 15 min, coordonator cu coalescing, `Fetch now`, retenție, deduplicare în două trepte, cu teste.
3. **Conectori autorizați:** RSS/API generic configurabil, schelete Reuters Connect și FT (dezactivate fără credențiale), ScraplingConnector limitat la allowlist, feeduri oficiale (Fed, ECB, BLS, BEA, EIA, FDA, SEC etc.).
4. **Finisare:** căutare FTS5, filtre, sănătatea surselor, loguri, README și LaunchAgent.

## Dependențe și motivul lor

| Pachet | De ce |
|---|---|
| fastapi, uvicorn | indicate în brief: API și server local |
| sqlalchemy, alembic | indicate în brief: ORM și migrații |
| apscheduler 3.x | indicat în brief: refresh la 15 min |
| pydantic-settings | citește `.env` tipat (vine deja cu FastAPI/pydantic) |
| pyyaml | configurarea surselor, categoriilor și entităților, editabilă de utilizator |
| httpx | client HTTP cu timeout, ETag și `If-Modified-Since`; e și clientul de test FastAPI |
| feedparser | indicat în brief: RSS/Atom |
| datasketch | indicat în brief: MinHash/LSH pentru candidați |
| trafilatura (opțional, `[extract]`) | indicat în brief: doar pentru surse `indexable_content` |
| scrapling (opțional, `[scrapling]`, doar parser) | indicat în brief: fără extra `fetchers` (acela aduce Playwright) |
| @radix-ui/react-collapsible | componentă accesibilă pentru listele pliabile; singura dependență UI la runtime |
| vitest, @testing-library/react, jsdom (dev) | testarea componentelor și a fluxurilor UI (`Fetch now`, filtre, carduri) |

Toolchain: Node LTS e instalat local în `.tools/` (binarul oficial, cu checksum verificat), pentru că Homebrew compila LLVM din surse pe macOS 14.
