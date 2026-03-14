#!/bin/bash

# ==============================================================================
# Documentação e Comandos Básicos do Docker
# ==============================================================================

# Para usar este script, primeiro dê permissão de execução:
# chmod +x docker.sh

# Mostra o menu de ajuda se nenhum argumento for passado
if [ -z "$1" ]; then
  echo "Uso: ./docker.sh [comando]"
  echo ""
  echo "Comandos disponíveis:"
  echo "  up      - Constrói a imagem e sobe o contentor em segundo plano (porta 3000)"
  echo "  down    - Para e remove o contentor criado"
  echo "  logs    - Mostra os logs do contentor em tempo real"
  echo "  sh      - Abre o terminal (shell) interativo dentro do contentor"
  echo "  restart - Reinicia o contentor"
  exit 1
fi

case "$1" in
  "up")
    echo "Subindo o contentor do back-end..."
    # Usa o docker-compose.yml para construir a imagem baseada no Dockerfile e rodar
    docker compose up --build -d
    echo "Contentor 'back_end' rodando. Acesse em http://localhost:3000"
    ;;
  "down")
    echo "Derrubando o contentor do back-end..."
    docker compose down
    ;;
  "logs")
    echo "Exibindo logs do contentor 'back_end' (Ctrl+C para sair)..."
    docker compose logs -f
    ;;
  "sh")
    echo "Acessando o terminal do contentor..."
    # 'app' é o nome do serviço definido no docker-compose.yml
    docker compose exec app sh
    ;;
  "restart")
    echo "Reiniciando o contentor..."
    docker compose restart
    ;;
  *)
    echo "Comando inválido. Execute ./docker.sh para ver a ajuda."
    ;;
esac