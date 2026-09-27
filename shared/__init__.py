"""Biblioteca comum aos dois microsserviços.

Contém apenas código sem estado: carga da configuração experimental, geração de
identificadores e timestamps. Não compartilha banco nem estado em tempo de
execução entre `orders-service` e `inventory-service` (RNF-003).
"""
