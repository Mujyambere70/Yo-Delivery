"""
chat_seed7.py - version 7: more starting examples for the chat model (written for this project), in the 7 languages
of the site. "bad" = a moderator would hide it: paying outside the site, asking for card data or codes, moving the
chat to another app to avoid the rules, insults. "ok" = normal questions of buyers and answers of sellers.
"""

BAD = [
    # English
    "pay me on paypal friends and family, not here", "send cash in an envelope and I post the item",
    "I can give 20% off if you pay by bank transfer outside the site", "add me on telegram for the real price",
    "text me on my number, this site takes too much commission", "give me your bank login and I refund you",
    "tell me the six digit code you just got", "what is the cvv on the back of your card",
    "pay with gift cards and send me the codes", "buy crypto and send it to this address",
    "you are useless, shut up", "what a dumb question, idiot", "go away loser", "scammer, I will find you",
    "you people are trash", "pay first to my personal upi id", "deposit to my mpesa number directly",
    "cancel the order and pay me in cash, we both save",
    # Russian
    "оплатите на сбп по моему номеру, не через сайт", "переведите предоплату на карту сбербанка",
    "назовите код из сообщения банка", "скажите три цифры на обороте карты", "давай в вотсап, там договоримся",
    "отмени заказ и заплати мне наличными", "ты дебил что ли", "отстань, придурок", "идиот, читай описание",
    "кидай деньги на киви кошелек", "пришли фото карты с двух сторон", "плати криптой на этот адрес",
    # French
    "payez-moi directement par virement, pas sur le site", "envoyez le code reçu par sms",
    "donnez-moi le numéro de votre carte", "parlons sur whatsapp, c'est moins cher", "tu es un idiot",
    "vendeur stupide, réponds", "envoie l'argent par western union", "paie avec des cartes cadeaux et donne les codes",
    "annule la commande et paie-moi en liquide", "imbécile, lis la description",
    # Spanish
    "págame directamente por transferencia, fuera de la web", "dime el código que te llegó por sms",
    "dame el número de tu tarjeta y el cvv", "hablemos por whatsapp, allí es más barato", "eres un idiota",
    "vendedor estúpido, contesta ya", "envía el dinero por western union", "cancela el pedido y págame en efectivo",
    "paga con tarjetas regalo y mándame los códigos", "tonto, lee la descripción",
    # Arabic
    "ادفع لي مباشرة خارج الموقع", "أرسل لي الرمز الذي وصلك في الرسالة", "أعطني رقم بطاقتك والرمز السري",
    "تواصل معي على واتساب السعر أرخص", "أنت غبي", "بائع أحمق رد علي", "حول المال عبر ويسترن يونيون",
    "ألغ الطلب وادفع لي نقدا",
    # Chinese
    "直接转账给我，不要在网站付款", "把短信验证码发给我", "告诉我你的银行卡号和安全码", "加我微信，那里更便宜",
    "你是白痴", "笨蛋卖家快回答", "用礼品卡付款，把卡号发我", "取消订单，私下付现金",
    # Kinyarwanda
    "nyishyura kuri momo yanjye, ntunyure ku rubuga", "mbwira kode wakiriye kuri sms", "mpa nimero ya karita yawe",
    "tuvugane kuri whatsapp, ni make kurushaho", "uri igicucu", "ucuruza w'umupfapfa, nsubiza",
    "ohereza amafaranga hanze y'urubuga", "hagarika komande unyishyure mu ntoki",
]

OK = [
    # English
    "is the price including delivery?", "do you have it in size 42?", "how long is the battery life?",
    "can you send it by Friday?", "is this the original brand?", "what colours do you have?",
    "I would like two pieces, is there stock?", "the order number is YO-1234, any news?", "does it come with a charger?",
    "can I pay on delivery?", "please call before you deliver", "thank you for the quick answer",
    "the item is very good, five stars", "is the book in English?", "how do I wash this shirt?",
    "can you wrap it as a gift?", "is the watch waterproof?", "can I change the address of my order?",
    "the parcel is late, can you check?", "sorry, I ordered the wrong size, can I exchange it?",
    # Russian
    "цена с доставкой?", "есть 42 размер?", "сколько держит батарея?", "сможете отправить до пятницы?",
    "это оригинал?", "какие есть цвета?", "можно оплатить при получении?", "позвоните перед доставкой, пожалуйста",
    "спасибо за быстрый ответ", "товар отличный, пять звёзд", "книга на английском?", "можно упаковать как подарок?",
    "можно поменять адрес заказа?", "посылка задерживается, проверьте, пожалуйста", "можно обменять на другой размер?",
    # French
    "le prix comprend la livraison ?", "vous l'avez en taille 42 ?", "pouvez-vous l'envoyer avant vendredi ?",
    "c'est la marque originale ?", "quelles couleurs avez-vous ?", "je peux payer à la livraison ?",
    "merci pour la réponse rapide", "très bon produit, cinq étoiles", "pouvez-vous faire un emballage cadeau ?",
    "le colis est en retard, vous pouvez vérifier ?",
    # Spanish
    "¿el precio incluye el envío?", "¿lo tienes en talla 42?", "¿puedes enviarlo antes del viernes?",
    "¿es la marca original?", "¿qué colores tienes?", "¿puedo pagar contra entrega?", "gracias por la respuesta rápida",
    "muy buen producto, cinco estrellas", "¿puedes envolverlo para regalo?", "el paquete llega tarde, ¿puedes revisar?",
    # Arabic
    "هل السعر يشمل التوصيل؟", "هل يوجد مقاس ٤٢؟", "هل يمكن الإرسال قبل الجمعة؟", "هل هذا المنتج أصلي؟",
    "ما الألوان المتوفرة؟", "هل يمكن الدفع عند الاستلام؟", "شكرا على الرد السريع", "منتج ممتاز خمس نجوم",
    "هل يمكن تغليفه كهدية؟", "الطرد متأخر هل يمكنك التحقق؟",
    # Chinese
    "价格包含运费吗？", "有42码吗？", "周五之前能发货吗？", "这是正品吗？", "有哪些颜色？", "可以货到付款吗？",
    "谢谢你的快速回复", "产品很好，五星", "可以包装成礼物吗？", "包裹晚了，能帮我查一下吗？",
    # Kinyarwanda
    "igiciro kirimo n'ubwikorezi?", "mufite nomero 42?", "mwakohereza mbere yo ku wa gatanu?",
    "ni ibyumwimerere?", "mufite amabara ayahe?", "nshobora kwishyura nyuma yo kwakira?",
    "murakoze gusubiza vuba", "igicuruzwa ni cyiza cyane", "mwabipfunyika nk'impano?",
    "ipaki yatinze, mwareba?",
    # asking to call or to be told something is normal (the phone number itself is hidden by the rules)
    "hello, can I call you about my order?", "call me when you reach the gate", "please call me before you come",
    "can I call you tomorrow morning?", "tell me the price with delivery", "show me another colour please",
    "let me know when it is shipped", "message me here when it is ready", "hello, is it still available?",
    "can you call me back after six?", "ring me when you are outside", "hello, thank you for the quick parcel",
    "позвоните мне, когда приедете", "можно вам позвонить?", "напишите мне, когда отправите",
    "appelez-moi à votre arrivée", "je peux vous appeler ?", "llámame cuando llegues", "¿puedo llamarte?",
    "اتصل بي عندما تصل", "到了请给我打电话", "我可以给你打电话吗？", "mumpamagare mugeze ku irembo",
    "nshobora kubahamagara?",
    # normal answers of sellers
    "yes, it works on gas and induction", "we send it the same day", "we ship tomorrow morning",
    "yes, size 42 is in stock", "the battery lasts about two days", "it comes with a one year warranty",
    "yes, it is the original brand", "we have it in black, blue and red", "yes, you can pay on delivery",
    "thank you for your order, it is packed", "sorry for the delay, the parcel left today",
    "you can return it within 7 days", "the price already includes the delivery", "yes, we can wrap it as a gift",
    "hello, yes it is available", "we send it today with Yo Delivery",
    "да, работает на газе и индукции", "отправим сегодня же", "да, 42 размер есть", "гарантия один год",
    "oui, il est disponible", "nous l'envoyons aujourd'hui", "sí, está disponible", "lo enviamos hoy mismo",
    "نعم، متوفر", "نرسله اليوم", "有货，今天发货", "是正品，保修一年", "yego, birahari", "turabyohereza uyu munsi",
]
