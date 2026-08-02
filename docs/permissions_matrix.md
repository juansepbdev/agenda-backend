# Matriz de permisos

| Rol | Agenda | Gestión | Configuración |
|---|---|---|---|
| ADMIN | Todos los eventos de su empresa | Usuarios, asesores, clientes y supervisiones | Total del tenant |
| SUPERVISOR | Sus asesores y su propia agenda | Según configuración de la empresa | No |
| ADVISOR | Solo su agenda | Clientes asociados y disponibilidad propia según configuración | No |

Ningún rol de tenant puede consultar otra empresa. Los permisos de objeto se aplican mediante los selectores de eventos y relaciones de supervisión.
