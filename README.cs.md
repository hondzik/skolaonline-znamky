# Škola OnLine — Známky (Home Assistant)

[English](README.md)

Čte známky dětí ze Škola OnLine do Home Assistantu. Jeden config entry na rodičovský účet,
jedna sensor entita na dítě.

> **Stav: raná verze / neověřeno proti reálnému účtu.** Integrace vychází z veřejné
> [OpenAPI dokumentace](https://libre-skolaonline.github.io/API-docs/) Škola OnLine, která
> nepopisuje, jak se u rodičovského účtu vrací seznam dětí — pokud se u vás integrace nechová
> podle popisu níže, založte prosím issue.

## Co integrace dělá

- Přihlásí se do Škola OnLine (OAuth2 password grant) a najde známky pro každé nakonfigurované dítě.
- Jedna entita na dítě, **stabilní napříč pololetími i školními roky** — na začátku nového
  pololetí nevznikají/nemizí entity.
- `state` = celkový průměr za aktuální pololetí (průměr vážených průměrů jednotlivých předmětů,
  stejná konvence jako na vysvědčení).
- Atributy: aktuální pololetí/školní rok, rozpis po předmětech s průměrem každého předmětu a
  jeho posledními známkami (pár na předmět, ne celá historie — viz níže).
- Předmět se v rozpisu objeví, i než z něj padne první známka — kompletní seznam předmětů se
  bere z rozvrhu, ne jen z těch, které už mají hodnocení.
- Event `skolaonline_znamky_new_mark` se vystřelí při skutečně nové známce (sleduje se i přes
  restart HA, takže se staré známky po restartu nehlásí znovu jako nové).
- Služba `skolaonline_znamky.get_marks` stáhne na vyžádání **kompletní** seznam známek (včetně
  tématu/slovního hodnocení, které v atributech nejsou) pro dítě/pololetí/předmět — určeno pro
  budoucí kartu "zobrazit všechny známky", ta ale není součástí této integrace.

## Proč není celá historie v atributech senzoru?

Recorder Home Assistantu ukládá u jednoho stavu jen omezené množství dat v atributech (~16 kB) a
nad tímto limitem je warningem zahodí. Držením jen krátkého nedávného seznamu na předmět v
senzoru a dotahováním zbytku přes službu `get_marks` na vyžádání zůstane každá entita bezpečně
pod limitem bez ohledu na to, kolik známek se za školní rok nahromadí.

## Instalace (HACS)

[![My Home Assistant](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?repository=skolaonline-znamky&owner=hondzik&category=Integration)

Přidejte tento repozitář jako vlastní HACS repozitář (kategorie: Integration), nainstalujte,
restartujte Home Assistant a přidejte integraci přes Nastavení → Zařízení a služby.

## Nastavení

1. Zadejte uživatelské jméno a heslo k rodičovskému účtu Škola OnLine.
2. Vyberte, které z automaticky nalezených dětí se mají sledovat. Pokud se dítě nenajde
   automaticky (viz poznámka o stavu výše), lze ho přidat ručně podle ID žáka.

Volby (Nastavení → Zařízení a služby → tato integrace → Konfigurovat) umožňují později změnit
sledované děti, interval stahování a kolik posledních známek na předmět se drží v atributech.

## Známá omezení

- Jako živý stav entity jsou dostupné jen známky za *aktuální* pololetí. Historická pololetí
  jsou dosažitelná přes službu `get_marks`, ne jako samostatné entity.
- Lovelace karta není součástí tohoto repozitáře — viz
  [`hondzik/skolaonline-znamky-ui`](https://github.com/hondzik/skolaonline-znamky-ui), kartu
  pro zobrazení známek z téhle integrace.

## Poděkování

Inspirováno integracemi [`schizza/bakalari-ha`](https://github.com/schizza/bakalari-ha) a
[`VitisEK/home-assistant-bakalari`](https://github.com/VitisEK/home-assistant-bakalari) (stejný
problém pro systém Bakaláři) a integrací
[`elvisek2020/hacs-calendar_skolaonline`](https://github.com/elvisek2020/hacs-calendar_skolaonline)
(rozvrh pro Škola OnLine, vzor pro auth flow).
