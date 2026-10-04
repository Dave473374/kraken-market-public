# SOL500 — 7-dnevna forward simulacija

**Samo navidezni denar. Nobenih pravih naročil, exchange ključev ali zasebnih Kraken API-jev.**

ID: `SOL500-FWD-20261004-v1`.
Obdobje: **4. 10. 2026 ob 09:00 do 11. 10. 2026 ob 09:00**, Europe/Ljubljana (07:00 UTC).
Začetni kapital: **500 EUR v gotovini**, ločeno od vseh resničnih sredstev in Alpha Watch.
Par: **Kraken SOL/EUR**, international spot. Ta model ni uporabnikov običajni Kraken Convert.

## Izvedba in ločenost

Koda je `research/paper-sol500-20261004/engine.py` na `main`.
Ločen workflow je `.github/workflows/paper-sol500-20261004.yml`.
Stanje, poročilo, protokol in opažanja so na veji **`paper-sol500-20261004`**, pod isto raziskovalno mapo.
Obstoječi produkcijski collectorji, Alpha Watch, DCA, dejanski portfelj, NiceHash BUY Radar in VMM niso spremenjeni.
Novi workflow ne zapisuje v main/core-live ali v obstoječe produkcijske podatke.

Nominalni pregled: vsakih 5 minut (UTC minute 2,7,12,...57). GitHub Actions lahko zamudi ali izpusti zagon. To ni neprekinjeno spremljanje in ni jamstvo petminutne odzivnosti.
Prvi zdravi zagon je bil preverjen 4.10.2026 ob 08:47:09 lokalno: DATA_OK, ARMED, cash 500 EUR, brez pozicije in poslov. Prvi aktivni pregled po 09:00 dejansko določi začetno tržno opažanje; zamik se zapiše, konec ostane fiksen.

## Zamrznjena strategija

En sam long spot položaj, brez vzvoda, shortanja, martingala ali dodatnih nakupov v izgubo.
Nakupni signal uporablja samo zaključene 1h sveče. Cena mora biti nad 50-urnim enostavnim povprečjem, 20-urno povprečje nad 50-urnim in višje kot tri ure prej. Volumen zadnje sveče mora doseči vsaj 80% povprečja predhodnih 20 sveč. Poleg tega potrebujemo preboj najvišje cene predhodnih šestih sveč ALI odboj od 20-urnega povprečja z zeleno svečo in višjim zaključkom od prejšnjega.
Nakup je dovoljen le ob prvem opažanju nove zaključene sveče, največ 15 minut po njenem koncu. Ni naknadnega vstavljanja zamujenih vstopov. Največ dva vstopa v drsečih 24 urah. Največji spread za vstop je 0,30%.

Stop razdalja: večja od 3% in 2,5-kratnika ATR14 glede na vstopno ceno; nad 6% se posel izpusti. Velikost se izračuna iz **načrtovane izgube največ min(10 EUR,2% neto vrednosti računa)**, vključno z modeliranimi stroški izstopa na stopu. Vložek ne sme presegati denarja ali 80% neto računa. Ob začetnih 500 EUR je zaradi risk-sizing pravil navadno približno 132–216 EUR, ne vseh 500 EUR.

Ob opaženem bidu vsaj 6% nad vstopno ceno se proda polovica. Preostanek dobi stroškovno prilagojen breakeven stop in 3% trailing od najvišjega **opaženega** bida; nima zgornjega profitnega cilja. Tudi dva zaključka pod pripadajočim SMA20 ali 72 ur držanja zapreta pozicijo. Po zaključku sledi triurni premor; po treh zaporednih izgubah 24-urni premor.

Padec neto vrednosti 4% od začetka lokalnega dne sproži izstop in premor do naslednjega dne. Neto vrednost pri ali pod 450 EUR sproži trajno ustavitev novih poslov. **To so sprožilci, ne zajamčene zgornje meje izgube.** Zaradi zamikov ali pomanjkanja podatkov je lahko dejanska simulirana izguba večja.

## Model stroškov in izvršitve

Fiksna predpostavka: javno objavljena začetna Kraken Pro taker provizija **0,80% na vsaki strani**. Ne predpostavljamo uporabnikovih popustov ali članstva. Vir preverjen 4.10.2026: https://www.kraken.com/features/fee-schedule .
Nakup se modelira skozi opaženo knjigo asks, prodaja skozi bids. Nato se na vsaki strani doda **0,10% neugodnega zdrsa** in provizija. Spread zato ni izpuščen in se ne prišteva še enkrat kot enaka dodatna postavka. Upoštevani so metapodatki para, natančnost količin in minimalni vstop.
Neto vrednost odprte pozicije že odšteje modelirane stroške takojšnjega izstopa. Davki in hosting niso vključeni.

**Stopi niso bili posredovani na borzo.** Sprožijo se pri dejansko opaženem bidu, zato med pregledi ni hipotetičnih idealnih izvršitev. Ne rekonstruiramo zamujenih donosnih poslov za nazaj. Primanjkljaji in napake se beležijo; brez podatkov ni izmišljenih cen, prodaj ali dobička.
Zadnja nezaključena sveča ni uporabljena: https://docs.kraken.com/api-reference/market-data/get-ohlc-data .

## Konec in primerjava

Zadnjo uro ni novih vstopov. Odprta pozicija se zapre pri prvem veljavnem opažanju po fiksnem koncu; dejanski čas in zamik sta prikazana. Nedosegljivi podatki pomenijo nezaključeno/omejeno poročilo, ne izmišljene zadnje prodaje.
Workflow je konfiguriran, da 11.10.2026 po 10:30 lokalno izklopi samo samega sebe, ne drugih workflowov.

Ločeni primerjavi kupi-in-drži modelirata začetni nakup SOL za 500 EUR in za 400 EUR (100 EUR ostane v gotovini), ob istih stroških. To nista dodatna sredstva bota in nista primerjavi enakega tveganja.

Urno obvestilno opravilo samo bere dnevnik, sporoča nove simulirane posle in težave ter zvečer dnevni pregled. Posebno opravilo 11.10.2026 dopoldne pripravi končni pregled. Nobeno od teh opravil ne odloča o novih poslih ali spreminja pravil.
Sedem dni, malo poslov ali pozitiven rezultat niso dokaz trajne prednosti in ne dovoljujejo samodejnega prehoda na pravi denar.

## Preverjanje

14 lokalnih determinističnih testov je uspelo. Ti preverjajo računovodstvo, vstop brez pogleda v prihodnost, dedupe, stroške in velikost, stop po opaženi ceni, delno prodajo/trailing, fiksni konec, vrzeli, zamujen signal in preprečevanje ponastavitve ob napačnem protokolu. To niso testi profitabilnosti.
SHA256 preverjene in objavljene kode: `4461a21a603f73c6615310f0646c262c60ae11f3522c71098a6eb108afa20192`.
Zamrznjeni protocol hash: `394eafafbe6a51a231b989cedd11401e8a4f438c93924ea5194e072bd6174d0a`.

Vsak posel ima status `MODELLED_PAPER_FILL_NOT_EXECUTED`, čas opažanja, količino, ceno, fee, cash delta, razlog ter sklic na opažanje. Pravila med tem preizkusom ne bodo prilagojena rezultatom.
