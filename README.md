# CityJá V4 — pacote completo de MVP

Incluído:
- cadastro/login de usuários e comerciantes;
- estabelecimentos;
- fotos/campo de foto no banco;
- busca e filtros;
- cidades da Grande Vitória;
- geolocalização do navegador;
- favoritos;
- avaliações;
- promoções;
- eventos;
- área do comerciante;
- solicitação de destaque;
- notificações preparadas no banco;
- base para Android/iPhone (web app responsivo).

## Rodar
pip install -r requirements.txt
python app.py
Abra http://127.0.0.1:5000

Demo: demo@cityja.local / cityja123

## O que ainda precisa de serviço externo para ser produção
Mapa real: Google Maps/Mapbox/OpenStreetMap + geocodificação.
Fotos: storage online/CDN.
Push: Firebase/APNs.
Pagamento do destaque: gateway como Mercado Pago/Stripe.
Publicação Android/iOS: empacotamento e contas das lojas.
Esses itens estão preparados na arquitetura, mas não foram falsamente configurados com chaves ou cobranças reais.
