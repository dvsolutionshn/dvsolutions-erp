PHONE_PREFIX_CHOICES = [
    ("504", "Honduras (+504)"),
    ("1", "Estados Unidos / Canadá (+1)"),
    ("52", "México (+52)"),
    ("501", "Belice (+501)"),
    ("502", "Guatemala (+502)"),
    ("503", "El Salvador (+503)"),
    ("505", "Nicaragua (+505)"),
    ("506", "Costa Rica (+506)"),
    ("507", "Panamá (+507)"),
    ("53", "Cuba (+53)"),
    ("509", "Haití (+509)"),
    ("1809", "República Dominicana (+1 809)"),
    ("1829", "República Dominicana (+1 829)"),
    ("1849", "República Dominicana (+1 849)"),
    ("1787", "Puerto Rico (+1 787)"),
    ("1939", "Puerto Rico (+1 939)"),
    ("1876", "Jamaica (+1 876)"),
    ("1242", "Bahamas (+1 242)"),
    ("1246", "Barbados (+1 246)"),
    ("1264", "Anguila (+1 264)"),
    ("1268", "Antigua y Barbuda (+1 268)"),
    ("1473", "Granada (+1 473)"),
    ("1649", "Islas Turcas y Caicos (+1 649)"),
    ("1664", "Montserrat (+1 664)"),
    ("1758", "Santa Lucía (+1 758)"),
    ("1767", "Dominica (+1 767)"),
    ("1784", "San Vicente y las Granadinas (+1 784)"),
    ("1868", "Trinidad y Tobago (+1 868)"),
    ("1869", "San Cristóbal y Nieves (+1 869)"),
    ("1345", "Islas Caimán (+1 345)"),
    ("1441", "Bermudas (+1 441)"),
    ("1284", "Islas Vírgenes Británicas (+1 284)"),
    ("1340", "Islas Vírgenes EE. UU. (+1 340)"),
    ("297", "Aruba (+297)"),
    ("599", "Curazao / Caribe Neerlandés (+599)"),
    ("590", "Guadalupe / San Martín / San Bartolomé (+590)"),
    ("596", "Martinica (+596)"),
    ("594", "Guayana Francesa (+594)"),
    ("508", "San Pedro y Miquelón (+508)"),
    ("299", "Groenlandia (+299)"),
    ("55", "Brasil (+55)"),
    ("54", "Argentina (+54)"),
    ("56", "Chile (+56)"),
    ("57", "Colombia (+57)"),
    ("58", "Venezuela (+58)"),
    ("593", "Ecuador (+593)"),
    ("51", "Perú (+51)"),
    ("591", "Bolivia (+591)"),
    ("595", "Paraguay (+595)"),
    ("598", "Uruguay (+598)"),
    ("592", "Guyana (+592)"),
    ("597", "Surinam (+597)"),
    ("500", "Islas Malvinas (+500)"),
    ("34", "España (+34)"),
    ("351", "Portugal (+351)"),
    ("33", "Francia (+33)"),
    ("39", "Italia (+39)"),
    ("49", "Alemania (+49)"),
    ("44", "Reino Unido / Isla de Man / Jersey / Guernsey (+44)"),
    ("353", "Irlanda (+353)"),
    ("350", "Gibraltar (+350)"),
    ("31", "Países Bajos (+31)"),
    ("32", "Bélgica (+32)"),
    ("352", "Luxemburgo (+352)"),
    ("41", "Suiza (+41)"),
    ("43", "Austria (+43)"),
    ("45", "Dinamarca (+45)"),
    ("46", "Suecia (+46)"),
    ("47", "Noruega (+47)"),
    ("358", "Finlandia (+358)"),
    ("354", "Islandia (+354)"),
    ("298", "Islas Feroe (+298)"),
    ("372", "Estonia (+372)"),
    ("371", "Letonia (+371)"),
    ("370", "Lituania (+370)"),
    ("48", "Polonia (+48)"),
    ("420", "República Checa (+420)"),
    ("421", "Eslovaquia (+421)"),
    ("36", "Hungría (+36)"),
    ("40", "Rumanía (+40)"),
    ("359", "Bulgaria (+359)"),
    ("385", "Croacia (+385)"),
    ("386", "Eslovenia (+386)"),
    ("381", "Serbia (+381)"),
    ("382", "Montenegro (+382)"),
    ("383", "Kosovo (+383)"),
    ("387", "Bosnia y Herzegovina (+387)"),
    ("389", "Macedonia del Norte (+389)"),
    ("355", "Albania (+355)"),
    ("30", "Grecia (+30)"),
    ("90", "Turquía (+90)"),
    ("357", "Chipre (+357)"),
    ("356", "Malta (+356)"),
    ("377", "Mónaco (+377)"),
    ("376", "Andorra (+376)"),
    ("378", "San Marino (+378)"),
    ("379", "Ciudad del Vaticano (+379)"),
    ("423", "Liechtenstein (+423)"),
    ("380", "Ucrania (+380)"),
    ("373", "Moldavia (+373)"),
    ("375", "Bielorrusia (+375)"),
    ("7", "Rusia / Kazajistán (+7)"),
    ("995", "Georgia (+995)"),
    ("374", "Armenia (+374)"),
    ("994", "Azerbaiyán (+994)"),
]


def normalize_phone_prefix(value, default="504"):
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if digits:
        return digits
    text = str(value or "")
    for code, label in PHONE_PREFIX_CHOICES:
        if code in text or label == text:
            return code
    return default


def apply_phone_prefix(number, prefix):
    digits = "".join(ch for ch in str(number or "") if ch.isdigit())
    code = normalize_phone_prefix(prefix)
    if not digits:
        return ""
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith(code):
        return digits
    if len(digits) <= 10:
        return f"{code}{digits}"
    return digits
