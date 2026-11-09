"""Temperature conversion for the synthetic factory; exported data uses °F."""


def fahrenheit(celsius):
    return round(celsius * 9 / 5 + 32, 3)


def simulation_celsius(fahrenheit_value):
    # The original physical simulation uses temperatures rounded to 0.01 °C.
    return round((fahrenheit_value - 32) * 5 / 9, 2)
