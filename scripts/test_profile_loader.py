from src.config.profile_loader import load_profile


def main():
    profile = load_profile("cbmm")

    print("✅ Perfil cargado correctamente")
    print("ERP:", profile["erp"]["name"])
    print("Base URL:", profile["erp"]["base_url"])
    print("Login URL:", profile["login"]["url"])
    print("Módulos iniciales:")

    for module in profile["exploration"]["start_modules"]:
        print("-", module)


if __name__ == "__main__":
    main()