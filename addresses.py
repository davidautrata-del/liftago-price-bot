"""Pool pražských adres (název pro našeptávač v appce, lat, lon).

Souřadnice jsou přibližné a slouží jen k výběru dvojic podle vzdálenosti.
Textové adresy ověř v appce: když našeptávač adresu nenajde, bot to zapíše
do sloupce Poznámka a tu adresu můžeš z poolu vyřadit nebo upravit.
"""

ADDRESSES = [
    # centrum
    ("Václavské náměstí 1, Praha 1", 50.0810, 14.4280),
    ("Staroměstské náměstí 1, Praha 1", 50.0875, 14.4213),
    ("Hlavní nádraží, Wilsonova 8, Praha 2", 50.0833, 14.4350),
    ("náměstí Republiky 1, Praha 1", 50.0883, 14.4311),
    ("Národní 28, Praha 1", 50.0814, 14.4160),
    ("Malostranské náměstí 1, Praha 1", 50.0883, 14.4036),
    ("Karlovo náměstí 1, Praha 2", 50.0762, 14.4186),
    ("Vyšehrad, V Pevnosti 5, Praha 2", 50.0640, 14.4190),
    # Vinohrady, Žižkov, Libeň
    ("náměstí Míru 1, Praha 2", 50.0752, 14.4377),
    ("Jiřího z Poděbrad 1, Praha 3", 50.0777, 14.4501),
    ("Flora, Vinohradská 151, Praha 3", 50.0777, 14.4627),
    ("Olšanské náměstí 1, Praha 3", 50.0838, 14.4565),
    ("Mahlerovy sady 1, Praha 3", 50.0817, 14.4506),
    ("Palmovka, Praha 8", 50.1043, 14.4750),
    # Holešovice, Letná, Dejvice
    ("Strossmayerovo náměstí 1, Praha 7", 50.1005, 14.4343),
    ("Výstaviště Praha, Za Elektrárnou 419, Praha 7", 50.1051, 14.4310),
    ("Letenské náměstí 1, Praha 7", 50.0964, 14.4235),
    ("Vítězné náměstí 1, Praha 6", 50.1005, 14.3935),
    ("Hradčanská, Badeniho 1, Praha 6", 50.0970, 14.4025),
    ("Petřiny, Praha 6", 50.0905, 14.3510),
    # Smíchov, jihozápad
    ("Anděl, Nádražní 1, Praha 5", 50.0706, 14.4035),
    ("Kříženeckého náměstí 1, Praha 5", 50.0344, 14.3708),
    ("Nemocnice Motol, V Úvalu 84, Praha 5", 50.0735, 14.3406),
    ("Stodůlky, Praha 13", 50.0471, 14.3330),
    ("Zličín, Praha 5", 50.0537, 14.2917),
    ("Letiště Václava Havla, Aviatická 1, Praha 6", 50.1008, 14.2600),
    # jih, Pankrác, Chodov, Háje
    ("Pankrác, Na Pankráci 1, Praha 4", 50.0508, 14.4390),
    ("Budějovická 1, Praha 4", 50.0443, 14.4491),
    ("Kačerov, Praha 4", 50.0420, 14.4590),
    ("Westfield Chodov, Roztylská 19, Praha 4", 50.0314, 14.4916),
    ("Háje, Praha 11", 50.0307, 14.5276),
    ("Modřany, náměstí Generála Kutlvašra, Praha 12", 50.0017, 14.4150),
    ("Zbraslavské náměstí, Praha 5", 49.9760, 14.3790),
    # sever, východ
    ("Kobylisy, Praha 8", 50.1263, 14.4655),
    ("OC Letňany, Veselská 663, Praha 9", 50.1262, 14.5086),
    ("Prosek, Praha 9", 50.1105, 14.5030),
    ("Černý Most, Praha 9", 50.1085, 14.5744),
    ("Letecké muzeum Kbely, Mladoboleslavská, Praha 9", 50.1236, 14.5392),
]
