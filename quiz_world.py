"""
quiz_world.py - version 5: the daily quiz in all 7 languages of the shop (English and Russian came in version 3).
12 questions per language (5 are asked every day). The same facts as the English bank, written for each language.
Format: (topic, question, [4 options], index of the right option, explanation).
"""

QUESTIONS_FR = [
    ("Géographie", "Quelle est la capitale du Rwanda ?", ["Kigali", "Butare", "Gisenyi", "Kampala"], 0,
     "Kigali est la capitale et la plus grande ville du Rwanda."),
    ("Géographie", "Quel fleuve traverse Krasnoïarsk ?", ["Ob", "Ienisseï", "Léna", "Volga"], 1,
     "L'Ienisseï est l'un des plus longs fleuves de Sibérie."),
    ("Géographie", "Chandigarh est la capitale de quels deux États indiens ?",
     ["Pendjab et Haryana", "Haryana et Himachal Pradesh", "Pendjab et Rajasthan", "Delhi et Haryana"], 0,
     "Chandigarh est un territoire de l'Union et la capitale du Pendjab et de l'Haryana."),
    ("Géographie", "Quel est le plus grand océan ?", ["Atlantique", "Indien", "Pacifique", "Arctique"], 2,
     "L'océan Pacifique est le plus grand et le plus profond."),
    ("Géographie", "Quel est le plus grand pays du monde par la superficie ?",
     ["Canada", "Chine", "Russie", "États-Unis"], 2, "La Russie couvre plus de 17 millions de kilomètres carrés."),
    ("Géographie", "Dans quel pays se trouve le lac Baïkal, le lac le plus profond du monde ?",
     ["Russie", "Mongolie", "Kazakhstan", "Chine"], 0, "Le lac Baïkal est dans le sud de la Sibérie, en Russie."),
    ("Géographie", "Quelle ville indienne est appelée la « Silicon Valley de l'Inde » ?",
     ["Bengaluru", "Hyderabad", "Pune", "Chennai"], 0, "Bengaluru est connue pour ses nombreuses entreprises informatiques."),
    ("Technologie", "Que signifie « HTTP » ?", ["HyperText Transfer Protocol", "High Transfer Text Process",
                                                  "Hyperlink Transfer Technology", "Home Tool Transfer Protocol"], 0,
     "HTTP est le protocole que les navigateurs utilisent pour obtenir les pages web."),
    ("Technologie", "En Python, quel mot-clé commence une fonction ?", ["func", "def", "function", "define"], 1,
     "def nom(arguments): commence une fonction en Python."),
    ("Technologie", "Que signifie SQL ?", ["Structured Query Language", "Simple Question Language",
                                           "Server Quick Logic", "Sequential Query List"], 0,
     "SQL est le langage des bases de données relationnelles comme PostgreSQL et SQLite."),
    ("Technologie", "Qui a créé le langage Python ?", ["Guido van Rossum", "Linus Torvalds", "James Gosling",
                                                       "Dennis Ritchie"], 0, "Guido van Rossum a publié Python en 1991."),
    ("Technologie", "Combien de bits y a-t-il dans un octet ?", ["4", "8", "16", "32"], 1, "Un octet = 8 bits."),
]

QUESTIONS_ES = [
    ("Geografía", "¿Cuál es la capital de Ruanda?", ["Kigali", "Butare", "Gisenyi", "Kampala"], 0,
     "Kigali es la capital y la ciudad más grande de Ruanda."),
    ("Geografía", "¿Qué río pasa por Krasnoyarsk?", ["Ob", "Yeniséi", "Lena", "Volga"], 1,
     "El Yeniséi es uno de los ríos más largos de Siberia."),
    ("Geografía", "Chandigarh es la capital de ¿qué dos estados de la India?",
     ["Panyab y Haryana", "Haryana e Himachal Pradesh", "Panyab y Rajastán", "Delhi y Haryana"], 0,
     "Chandigarh es un territorio de la Unión y la capital de Panyab y de Haryana."),
    ("Geografía", "¿Cuál es el océano más grande?", ["Atlántico", "Índico", "Pacífico", "Ártico"], 2,
     "El océano Pacífico es el más grande y el más profundo."),
    ("Geografía", "¿Cuál es el país más grande del mundo por superficie?",
     ["Canadá", "China", "Rusia", "Estados Unidos"], 2, "Rusia tiene más de 17 millones de kilómetros cuadrados."),
    ("Geografía", "¿En qué país está el lago Baikal, el lago más profundo del mundo?",
     ["Rusia", "Mongolia", "Kazajistán", "China"], 0, "El lago Baikal está en el sur de Siberia, en Rusia."),
    ("Geografía", "¿Qué ciudad india se llama a menudo «el Silicon Valley de la India»?",
     ["Bengaluru", "Hyderabad", "Pune", "Chennai"], 0, "Bengaluru es conocida por sus muchas empresas de informática."),
    ("Tecnología", "¿Qué significa «HTTP»?", ["HyperText Transfer Protocol", "High Transfer Text Process",
                                              "Hyperlink Transfer Technology", "Home Tool Transfer Protocol"], 0,
     "HTTP es el protocolo que usan los navegadores para obtener páginas web."),
    ("Tecnología", "En Python, ¿qué palabra clave empieza una función?", ["func", "def", "function", "define"], 1,
     "def nombre(argumentos): empieza una función en Python."),
    ("Tecnología", "¿Qué significa SQL?", ["Structured Query Language", "Simple Question Language",
                                           "Server Quick Logic", "Sequential Query List"], 0,
     "SQL es el lenguaje de las bases de datos relacionales como PostgreSQL y SQLite."),
    ("Tecnología", "¿Quién creó el lenguaje Python?", ["Guido van Rossum", "Linus Torvalds", "James Gosling",
                                                       "Dennis Ritchie"], 0, "Guido van Rossum publicó Python en 1991."),
    ("Tecnología", "¿Cuántos bits hay en un byte?", ["4", "8", "16", "32"], 1, "Un byte tiene 8 bits."),
]

QUESTIONS_AR = [
    ("الجغرافيا", "ما هي عاصمة رواندا؟", ["كيغالي", "بوتاري", "جيسيني", "كمبالا"], 0,
     "كيغالي هي عاصمة رواندا وأكبر مدنها."),
    ("الجغرافيا", "أي نهر يمر عبر مدينة كراسنويارسك؟", ["أوب", "ينيسي", "لينا", "الفولغا"], 1,
     "نهر ينيسي من أطول أنهار سيبيريا."),
    ("الجغرافيا", "شانديغار عاصمة أي ولايتين هنديتين؟",
     ["البنجاب وهاريانا", "هاريانا وهيماجل برديش", "البنجاب وراجستان", "دلهي وهاريانا"], 0,
     "شانديغار إقليم اتحادي وعاصمة البنجاب وهاريانا."),
    ("الجغرافيا", "ما هو أكبر محيط؟", ["الأطلسي", "الهندي", "الهادئ", "المتجمد الشمالي"], 2,
     "المحيط الهادئ هو الأكبر والأعمق."),
    ("الجغرافيا", "ما هي أكبر دولة في العالم من حيث المساحة؟", ["كندا", "الصين", "روسيا", "الولايات المتحدة"], 2,
     "مساحة روسيا أكثر من 17 مليون كيلومتر مربع."),
    ("الجغرافيا", "في أي دولة تقع بحيرة بايكال، أعمق بحيرة في العالم؟", ["روسيا", "منغوليا", "كازاخستان", "الصين"], 0,
     "تقع بحيرة بايكال في جنوب سيبيريا في روسيا."),
    ("الجغرافيا", "أي مدينة هندية تُسمّى «وادي السيليكون في الهند»؟", ["بنغالورو", "حيدر أباد", "بونه", "تشيناي"], 0,
     "تشتهر بنغالورو بشركات تقنية المعلومات الكثيرة."),
    ("التقنية", "ماذا يعني «HTTP»؟", ["HyperText Transfer Protocol", "High Transfer Text Process",
                                       "Hyperlink Transfer Technology", "Home Tool Transfer Protocol"], 0,
     "HTTP هو البروتوكول الذي تستخدمه المتصفحات لجلب صفحات الويب."),
    ("التقنية", "في بايثون، أي كلمة مفتاحية تبدأ بها الدالة؟", ["func", "def", "function", "define"], 1,
     "def name(arguments): تبدأ الدالة في بايثون."),
    ("التقنية", "ماذا يعني SQL؟", ["Structured Query Language", "Simple Question Language", "Server Quick Logic",
                                    "Sequential Query List"], 0,
     "SQL هي لغة قواعد البيانات العلائقية مثل PostgreSQL وSQLite."),
    ("التقنية", "من أنشأ لغة البرمجة بايثون؟", ["Guido van Rossum", "Linus Torvalds", "James Gosling",
                                                 "Dennis Ritchie"], 0, "نشر غيدو فان روسوم لغة بايثون عام 1991."),
    ("التقنية", "كم بت في البايت الواحد؟", ["4", "8", "16", "32"], 1, "البايت الواحد = 8 بت."),
]

QUESTIONS_ZH = [
    ("地理", "卢旺达的首都是哪座城市？", ["基加利", "布塔雷", "吉塞尼", "坎帕拉"], 0, "基加利是卢旺达的首都和最大城市。"),
    ("地理", "哪条河流经克拉斯诺亚尔斯克？", ["鄂毕河", "叶尼塞河", "勒拿河", "伏尔加河"], 1, "叶尼塞河是西伯利亚最长的河流之一。"),
    ("地理", "昌迪加尔是印度哪两个邦的首府？", ["旁遮普邦和哈里亚纳邦", "哈里亚纳邦和喜马偕尔邦", "旁遮普邦和拉贾斯坦邦",
                                       "德里和哈里亚纳邦"], 0, "昌迪加尔是中央直辖区，也是旁遮普邦和哈里亚纳邦的首府。"),
    ("地理", "最大的大洋是哪一个？", ["大西洋", "印度洋", "太平洋", "北冰洋"], 2, "太平洋是最大、最深的大洋。"),
    ("地理", "按面积计算，世界上最大的国家是哪个？", ["加拿大", "中国", "俄罗斯", "美国"], 2, "俄罗斯面积超过1700万平方公里。"),
    ("地理", "世界上最深的湖贝加尔湖在哪个国家？", ["俄罗斯", "蒙古", "哈萨克斯坦", "中国"], 0, "贝加尔湖位于俄罗斯西伯利亚南部。"),
    ("地理", "哪座印度城市常被称为“印度的硅谷”？", ["班加罗尔", "海得拉巴", "浦那", "金奈"], 0, "班加罗尔以众多IT公司而闻名。"),
    ("技术", "“HTTP”是什么的缩写？", ["HyperText Transfer Protocol", "High Transfer Text Process",
                                  "Hyperlink Transfer Technology", "Home Tool Transfer Protocol"], 0,
     "HTTP是浏览器获取网页所用的协议。"),
    ("技术", "在Python中，哪个关键字用来定义函数？", ["func", "def", "function", "define"], 1, "在Python中用 def 名称(参数): 定义函数。"),
    ("技术", "SQL是什么的缩写？", ["Structured Query Language", "Simple Question Language", "Server Quick Logic",
                              "Sequential Query List"], 0, "SQL是PostgreSQL和SQLite等关系数据库的语言。"),
    ("技术", "谁创造了Python编程语言？", ["Guido van Rossum", "Linus Torvalds", "James Gosling", "Dennis Ritchie"], 0,
     "吉多·范罗苏姆于1991年发布了Python。"),
    ("技术", "一个字节等于多少个比特（位）？", ["4", "8", "16", "32"], 1, "一个字节等于8位。"),
]

QUESTIONS_RW = [
    ("Ubumenyi bw'isi", "Umurwa mukuru w'u Rwanda ni uwuhe?", ["Kigali", "Butare", "Gisenyi", "Kampala"], 0,
     "Kigali ni umurwa mukuru w'u Rwanda kandi ni wo mujyi munini."),
    ("Ubumenyi bw'isi", "Ni uwuhe mugezi unyura i Krasnoyarsk?", ["Ob", "Yenisei", "Lena", "Volga"], 1,
     "Yenisei ni umwe mu migezi miremire yo muri Siberia."),
    ("Ubumenyi bw'isi", "Chandigarh ni umurwa mukuru wa leta ebyiri zo mu Buhinde. Ni izihe?",
     ["Punjab na Haryana", "Haryana na Himachal Pradesh", "Punjab na Rajasthan", "Delhi na Haryana"], 0,
     "Chandigarh ni umurwa mukuru wa Punjab na Haryana."),
    ("Ubumenyi bw'isi", "Ni iyihe nyanja nini kurusha izindi?", ["Atlantika", "Ubuhinde", "Pasifika", "Arigitika"], 2,
     "Inyanja ya Pasifika ni yo nini kandi ndende mu bujyakuzimu."),
    ("Ubumenyi bw'isi", "Ni ikihe gihugu kinini ku isi mu buso?", ["Kanada", "Ubushinwa", "Uburusiya", "Amerika"], 2,
     "Uburusiya bufite ubuso burenga kilometero kare miliyoni 17."),
    ("Ubumenyi bw'isi", "Ikiyaga cya Baikal, ikiyaga kirekire mu bujyakuzimu ku isi, kiri mu kihe gihugu?",
     ["Uburusiya", "Mongoliya", "Kazakhstan", "Ubushinwa"], 0, "Ikiyaga cya Baikal kiri mu majyepfo ya Siberia, mu Burusiya."),
    ("Ubumenyi bw'isi", "Ni uwuhe mujyi wo mu Buhinde bita «Silicon Valley y'u Buhinde»?",
     ["Bengaluru", "Hyderabad", "Pune", "Chennai"], 0, "Bengaluru izwiho kugira ibigo byinshi by'ikoranabuhanga."),
    ("Ikoranabuhanga", "«HTTP» bisobanura iki?", ["HyperText Transfer Protocol", "High Transfer Text Process",
                                                  "Hyperlink Transfer Technology", "Home Tool Transfer Protocol"], 0,
     "HTTP ni uburyo mudasobwa ikoresha mu kuzana paji za interineti."),
    ("Ikoranabuhanga", "Muri Python, ni irihe jambo ritangiza function?", ["func", "def", "function", "define"], 1,
     "def izina(arguments): itangiza function muri Python."),
    ("Ikoranabuhanga", "SQL bisobanura iki?", ["Structured Query Language", "Simple Question Language",
                                               "Server Quick Logic", "Sequential Query List"], 0,
     "SQL ni ururimi rwa data base nka PostgreSQL na SQLite."),
    ("Ikoranabuhanga", "Ni nde wakoze ururimi rwa porogaramu Python?", ["Guido van Rossum", "Linus Torvalds",
                                                                        "James Gosling", "Dennis Ritchie"], 0,
     "Guido van Rossum yasohoye Python mu 1991."),
    ("Ikoranabuhanga", "Byte imwe igira bit zingahe?", ["4", "8", "16", "32"], 1, "Byte imwe = bit 8."),
]

BANKS = {"fr": QUESTIONS_FR, "es": QUESTIONS_ES, "ar": QUESTIONS_AR, "zh": QUESTIONS_ZH, "rw": QUESTIONS_RW}


def ensure_quiz_languages(store):
    """Add the question bank of every language that has none yet (safe to run on every start)."""
    added = 0
    for lang, bank in BANKS.items():
        with store.db() as conn:
            if conn.execute("SELECT 1 FROM quiz_questions WHERE lang = ?", (lang,)).fetchone():
                continue
        for topic, q, options, answer, why in bank:
            store.save_question({"lang": lang, "topic": topic, "question": q, "explanation": why, "answer": answer,
                                 **{f"option{i}": o for i, o in enumerate(options)}})
            added += 1
    return added
