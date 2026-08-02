from rest_framework import serializers

from .models import User


class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False, min_length=8)

    class Meta:
        model = User
        fields = ("id", "email", "first_name", "last_name", "phone", "role", "is_active", "password", "created_at")
        read_only_fields = ("id", "is_active", "created_at")

    def create(self, validated_data):
        password = validated_data.pop("password")
        actor = self.context["request"].user
        return User.objects.create_user(company=actor.company, created_by=actor, updated_by=actor, password=password, **validated_data)

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if password:
            instance.set_password(password)
        instance.updated_by = self.context["request"].user
        instance.full_clean()
        instance.save()
        return instance
