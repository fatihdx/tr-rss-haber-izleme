# Katkı Rehberi

Teşekkürler! Bu proje küçük ve odaklıdır.

## Geliştirme
1. `config.sample.yaml` → `config.yaml` kopyalayın (bu dosya `.gitignore`'dadır, asla commit edilmez).
2. `python -m venv .venv && .venv/bin/pip install -r requirements.txt`
3. Hızlı test: `.venv/bin/python src/bot.py`

## Pull Request
- Tek bir konuya odaklı, küçük PR'lar tercih edilir.
- Sır içermeyin: token, chat ID, `state.db`, log dosyası eklemeyin.
- Filtre/keyword değişikliklerinde gerekçeyi PR açıklamasında belirtin.

## Kapsam
Sorunlar ve öneriler için Issues kullanın. Bot'un amacı README'deki
"Kapsam, Sınırlar & Sorumlu Kullanım" bölümüyle sınırlıdır.
